"""
Integration test fixtures — real PostgreSQL via testcontainers.

All integration tests use a single Postgres container per session,
with per-test schema teardown to keep tests isolated.
"""

import uuid

import jwt
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

import app.models  # noqa: F401 — registers all SQLModel tables with metadata
from app.config import get_settings
from app.models.user import User
from app.models.user_profile import UserProfile


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def asyncpg_url(postgres_container):
    raw = postgres_container.get_connection_url()
    # testcontainers returns psycopg2 URL; convert to asyncpg
    return raw.replace("psycopg2", "asyncpg").replace(
        "postgresql+asyncpg://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="session")
def sync_url(postgres_container):
    """Plain psycopg2 URL for LangGraph checkpointer (psycopg v3 connection string)."""
    raw = postgres_container.get_connection_url()
    return raw.replace("+psycopg2", "")


@pytest.fixture
async def db_session(asyncpg_url):
    """
    Per-test async session against a clean schema.
    Creates tables before test, drops after.
    """
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def patch_settings(asyncpg_url, monkeypatch):
    """Point get_settings() at the test database for all integration tests."""
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key")
    monkeypatch.setenv("CRON_SHARED_SECRET", "dev-cron-secret")
    # Reset the cached settings singleton between tests
    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)
    import app.database as db_mod

    monkeypatch.setattr(db_mod, "engine", None)
    monkeypatch.setattr(db_mod, "async_session_factory", None)


@pytest.fixture
async def seeded_user(db_session):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"test-{user_id}@local",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    profile = UserProfile(user_id=user_id, email=user.email)
    db_session.add(profile)
    await db_session.commit()
    return user, profile


@pytest.fixture
async def auth_headers(seeded_user):
    user, _ = seeded_user
    settings = get_settings()
    token = jwt.encode(
        {"sub": str(user.id), "aud": ["fastapi-users:auth"]},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}
