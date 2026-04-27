import logging
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from httpx_oauth.exceptions import GetIdEmailError
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api.applications import router as applications_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.internal_cron import router as cron_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.status import router as status_router
from app.api.users import router as users_router
from app.config import get_settings
from app.database import init_db

# Marker that tells GCP Cloud Error Reporting to ingest a Cloud Run log entry as a
# first-class error event. Combined with severity=ERROR + a Python traceback in the
# log payload, this gets errors auto-grouped in the Error Reporting UI without any
# third-party SDK. https://cloud.google.com/error-reporting/docs/formatting-error-messages
_REPORTED_ERROR_TYPE = (
    "type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent"
)


def _add_cloud_run_severity(logger, method, event_dict):
    # Cloud Run reads "severity" (uppercase) for log severity badges.
    # structlog's add_log_level writes "level" (lowercase) — copy it here.
    severity = event_dict.get("level", "info").upper()
    event_dict["severity"] = severity
    # Tag ERROR/CRITICAL records so Cloud Error Reporting picks them up as events.
    if severity in ("ERROR", "CRITICAL"):
        event_dict["@type"] = _REPORTED_ERROR_TYPE
    return event_dict


def configure_logging(settings) -> None:
    is_dev = settings.environment == "development"
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # Turn exc_info tuples (from log.aexception / log.aerror(..., exc_info=True))
        # into a multi-line traceback string under the "exception" key so GCP's
        # error-reporting heuristics can parse it.
        structlog.processors.format_exc_info,
    ]
    if not is_dev:
        processors.append(_add_cloud_run_severity)
    processors.append(
        structlog.dev.ConsoleRenderer() if is_dev else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    configure_logging(settings)
    log = structlog.get_logger()

    # Export LangSmith settings to os.environ for LangChain SDK (reads env vars directly)
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        await log.ainfo("langsmith.enabled", project=settings.langsmith_project)

    await log.ainfo("app.startup", environment=settings.environment)

    # Init DB (dev only — prod uses alembic)
    if settings.environment == "development":
        await init_db()

    # Init LangGraph checkpointer (psycopg v3, separate pool from SQLAlchemy asyncpg)
    psycopg_uri = str(settings.database_url).replace("+asyncpg", "")
    # setup() runs CREATE INDEX CONCURRENTLY which cannot run inside a pipeline,
    # so we run it once on a plain connection before opening the pipeline saver.
    from psycopg.errors import DuplicateTable

    async with AsyncPostgresSaver.from_conn_string(psycopg_uri) as setup_checkpointer:
        try:
            await setup_checkpointer.setup()
        except DuplicateTable:
            pass  # checkpoint tables already exist from a previous deploy
        # all other exceptions propagate and fail lifespan — loud failure at startup
    async with AsyncPostgresSaver.from_conn_string(psycopg_uri, pipeline=True) as checkpointer:
        app.state.checkpointer = checkpointer
        await log.ainfo("checkpointer.ready")

        yield

    await log.ainfo("app.shutdown")


app = FastAPI(title="Job Application Agent", lifespan=lifespan)


@app.exception_handler(GetIdEmailError)
async def _log_oauth_get_id_email_error(request: Request, exc: GetIdEmailError):
    # Without this handler, only the Python traceback reaches Cloud Run logs and
    # Google's actual error payload (e.g. "API not enabled", "invalid scope",
    # "user not in test users list") is lost on `exc.response`. Log it so future
    # OAuth callback failures are diagnosable from logs alone.
    log = structlog.get_logger()
    response = exc.response
    await log.aerror(
        "oauth.callback.get_id_email_failed",
        status_code=response.status_code,
        body=response.text[:1000],
        url=str(response.request.url) if response.request else None,
    )
    return JSONResponse(status_code=500, content={"detail": "OAuth profile lookup failed"})


_startup_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_startup_settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health(request: Request):
    settings = get_settings()
    scheduler = getattr(request.app.state, "scheduler", None)
    return {
        "status": "ok",
        "environment": settings.environment,
        "scheduler": "running" if scheduler and scheduler.running else "off",
    }


app.include_router(profile_router)
app.include_router(chat_router)
app.include_router(jobs_router)
app.include_router(applications_router)
app.include_router(documents_router)
app.include_router(cron_router)
app.include_router(status_router)
app.include_router(users_router)

# OAuth routes — always mounted in any environment that has Google credentials.
# In production we require them (Settings validator enforces this); in dev/test
# they may legitimately be absent and tests use the JWT path directly.
if _startup_settings.google_oauth_client_id and _startup_settings.google_oauth_client_secret:
    from app.api.auth import auth_backend, fastapi_users, get_google_oauth_client

    google_oauth_client = get_google_oauth_client()
    # Path must match the actual callback handler: fastapi-users mounts /callback on
    # the oauth router, and we mount the router at /auth/google → /auth/google/callback.
    _oauth_redirect_url = (
        f"{_startup_settings.public_base_url.rstrip('/')}/auth/google/callback"
        if _startup_settings.public_base_url
        else None
    )
    app.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            _startup_settings.jwt_secret.get_secret_value(),
            redirect_url=_oauth_redirect_url,
            # Link an OAuth login to an existing local user with the same email
            # instead of returning OAUTH_USER_ALREADY_EXISTS. Safe under OAuth-only
            # auth: Google proves the user owns the email, so claiming a seeded
            # row (smoke@panibrat.com, future admin seeds) is legitimate.
            associate_by_email=True,
            is_verified_by_default=True,
        ),
        prefix="/auth/google",
        tags=["auth"],
    )

# Dev-only endpoints for E2E testing
if _startup_settings.environment in ("development", "test"):
    from app.api.test_helpers import router as test_helpers_router

    app.include_router(test_helpers_router)

# SPA catch-all: serve React build. The route is always registered so it is
# testable via monkeypatching static_dir; the handler returns 404 if the
# build doesn't exist (dev without a build, or CI without frontend step).
static_dir = os.path.join(os.path.dirname(__file__), "static")
_assets_dir = os.path.join(static_dir, "assets")
if os.path.exists(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if full_path:
        candidate = os.path.join(static_dir, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
    index = os.path.join(static_dir, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")
