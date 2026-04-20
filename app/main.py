import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api.applications import router as applications_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.internal_cron import router as cron_router
from app.api.jobs import router as jobs_router
from app.api.profile import router as profile_router
from app.api.status import router as status_router
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

    # Init Sentry
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            traces_sample_rate=0.1,
            environment=settings.environment,
        )

    await log.ainfo("app.startup", environment=settings.environment)

    # Init DB (dev only — prod uses alembic)
    if settings.environment == "development":
        await init_db()

    # Init LangGraph checkpointer (psycopg v3, separate pool from SQLAlchemy asyncpg)
    psycopg_uri = str(settings.database_url).replace("+asyncpg", "")
    # setup() runs CREATE INDEX CONCURRENTLY which cannot run inside a pipeline,
    # so we run it once on a plain connection before opening the pipeline saver.
    async with AsyncPostgresSaver.from_conn_string(psycopg_uri) as setup_checkpointer:
        try:
            await setup_checkpointer.setup()
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise
    async with AsyncPostgresSaver.from_conn_string(psycopg_uri, pipeline=True) as checkpointer:
        app.state.checkpointer = checkpointer
        await log.ainfo("checkpointer.ready")

        yield

    await log.ainfo("app.shutdown")


app = FastAPI(title="Job Application Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
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

# Dev-only endpoints for E2E testing
settings = get_settings()
if settings.environment in ("development", "test"):
    from app.api.test_helpers import router as test_helpers_router
    app.include_router(test_helpers_router)

# Serve React build if it exists
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
