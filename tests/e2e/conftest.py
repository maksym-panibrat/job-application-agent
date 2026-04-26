"""
E2E test fixtures — full FastAPI app + real PostgreSQL via testcontainers.

The app lifespan runs normally (DB init + LangGraph checkpointer).
LLM calls are patched at the agent get_llm() level so no real API calls are made.
"""

import uuid as _uuid
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

import app.models  # noqa: F401 — registers all SQLModel tables
from app.api.deps import get_current_profile, get_current_user
from app.database import get_db
from app.models.user import User
from app.models.user_profile import UserProfile


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

    Seeds a single test user + profile and dependency-overrides
    get_current_profile / get_current_user so every request inside the
    test resolves to that user. (No JWT needed; the override bypasses
    deps.py.)
    """
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ADZUNA_APP_ID", "fake-app-id")
    monkeypatch.setenv("ADZUNA_API_KEY", "fake-api-key")
    monkeypatch.setenv("JSEARCH_API_KEY", "fake-jsearch-key")
    # Disable public sources so e2e tests only see sources explicitly mocked
    # in the test (adzuna, jsearch). Without this, job_sync_service hits the
    # real remotive/remoteok/arbeitnow HTTP APIs, polluting assertions.
    monkeypatch.setenv("REMOTIVE_ENABLED", "false")
    monkeypatch.setenv("REMOTEOK_ENABLED", "false")
    monkeypatch.setenv("ARBEITNOW_ENABLED", "false")
    monkeypatch.setenv("GREENHOUSE_BOARD_ENABLED", "false")

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
    from app.main import app as fastapi_app

    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    # Seed a test user + profile
    factory = async_sessionmaker(engine, expire_on_commit=False)
    test_user_id = _uuid.uuid4()
    async with factory() as session:
        user = User(
            id=test_user_id,
            email=f"e2e-{test_user_id}@local",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            hashed_password="",
        )
        session.add(user)
        profile = UserProfile(user_id=test_user_id, email=user.email)
        session.add(profile)
        await session.commit()
        await session.refresh(user)
        await session.refresh(profile)

    # Dependency-override the auth chain.
    # `get_current_profile` is what most endpoints depend on; overriding it
    # shortcircuits the JWT decode in `get_current_user`. We override both
    # for safety in case some endpoint depends on `get_current_user` directly.
    #
    # The profile override re-fetches from DB on each request so that tests
    # that mutate the profile (e.g. PATCH /api/profile) see the updated state
    # on subsequent GET calls. The user override can safely return the captured
    # object because user rows are immutable in e2e tests.
    from fastapi import Depends as _Depends
    from sqlalchemy.ext.asyncio import AsyncSession

    async def _override_profile(session: AsyncSession = _Depends(get_db)) -> UserProfile:
        from app.services import profile_service

        return await profile_service.get_or_create_profile(test_user_id, session)

    fastapi_app.dependency_overrides[get_current_profile] = _override_profile
    fastapi_app.dependency_overrides[get_current_user] = lambda: user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_current_profile, None)
        fastapi_app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
