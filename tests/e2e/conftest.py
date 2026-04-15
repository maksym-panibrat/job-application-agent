"""
E2E test fixtures — full FastAPI app + real PostgreSQL via testcontainers.

The app lifespan runs normally (DB init + LangGraph checkpointer).
LLM calls are patched at the agent get_llm() level so no real API calls are made.
"""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

import app.models  # noqa: F401 — registers all SQLModel tables


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def asyncpg_url(postgres_container):
    raw = postgres_container.get_connection_url()
    return raw.replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
def psycopg_url(postgres_container):
    """Plain psycopg v3 URL for LangGraph checkpointer."""
    raw = postgres_container.get_connection_url()
    return raw.replace("+psycopg2", "")


def _make_fake_onboarding_llm():
    """Fake LLM for the onboarding agent — returns a simple greeting."""
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(
        content="Welcome! I've noted your preferences. What roles are you targeting?"
    )
    llm.bind_tools.return_value = llm
    return llm


@pytest.fixture
async def test_app(asyncpg_url, psycopg_url, monkeypatch):
    """
    Full FastAPI app configured against the test DB.
    LLM calls are mocked. Yields an httpx.AsyncClient.
    """
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # Reset settings singleton so the env vars above take effect
    import app.config as cfg
    monkeypatch.setattr(cfg, "_settings", None)
    import app.database as db_mod
    monkeypatch.setattr(db_mod, "engine", None)
    monkeypatch.setattr(db_mod, "async_session_factory", None)

    # Patch LLM in onboarding agent
    monkeypatch.setattr(
        "app.agents.onboarding.get_llm",
        lambda: _make_fake_onboarding_llm(),
    )

    # Ensure schema is clean before each test
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.main import app as fastapi_app
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        yield client
