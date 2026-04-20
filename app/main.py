import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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


def configure_logging(settings) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            (
                structlog.dev.ConsoleRenderer()
                if settings.environment == "development"
                else structlog.processors.JSONRenderer()
            ),
        ],
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

    # Init Sentry — log confirmation so operators can verify it's active in production
    if settings.sentry_dsn:
        try:
            dsn_val = settings.sentry_dsn.get_secret_value()
            sentry_sdk.init(
                dsn=dsn_val,
                traces_sample_rate=0.1,
                environment=settings.environment,
            )
            await log.ainfo("sentry.enabled", dsn_suffix=dsn_val[-4:])
        except Exception as exc:
            await log.awarning("sentry.init_failed", error=str(exc))
    else:
        await log.ainfo("sentry.disabled", reason="no_dsn_configured")

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

# OAuth routes — only mount if credentials are configured
if _startup_settings.google_oauth_client_id and _startup_settings.google_oauth_client_secret:
    from app.api.auth import auth_backend, fastapi_users, get_google_oauth_client

    google_oauth_client = get_google_oauth_client()
    app.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            _startup_settings.jwt_secret.get_secret_value(),
            redirect_url="/auth/callback",
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
