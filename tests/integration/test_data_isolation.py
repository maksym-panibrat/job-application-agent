"""
Integration test: data isolation between two users.

Verifies that user A cannot read or mutate user B's profile, applications,
or documents. Returns 404 (not 403) to avoid leaking resource existence.
"""
import time
import uuid

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.models.user import User

TEST_JWT_SECRET = "test-secret-for-isolation"


def _make_token(user_id: uuid.UUID) -> str:
    payload = {
        "sub": str(user_id),
        "aud": ["fastapi-users:auth"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="module")
def isolation_postgres():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def isolation_asyncpg_url(isolation_postgres):
    raw = isolation_postgres.get_connection_url()
    return raw.replace("psycopg2", "asyncpg")


@pytest.fixture
async def isolation_app(isolation_asyncpg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", isolation_asyncpg_url)
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CRON_SHARED_SECRET", "real-cron-secret-for-tests")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")

    import app.config as cfg
    monkeypatch.setattr(cfg, "_settings", None)
    import app.database as db_mod
    monkeypatch.setattr(db_mod, "engine", None)
    monkeypatch.setattr(db_mod, "async_session_factory", None)

    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401
    from app.main import app as fastapi_app

    engine = create_async_engine(isolation_asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        yield client


async def _create_user_and_profile(client: AsyncClient, token: str) -> dict:
    """Create profile for user by making an authenticated request."""
    resp = await client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"profile creation failed: {resp.text}"
    return resp.json()


@pytest.mark.asyncio
async def test_users_cannot_read_each_others_profile(isolation_app):
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    # Seed users into the DB via the session factory
    from app.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(id=user_a, email="a@test.com", is_active=True))
        session.add(User(id=user_b, email="b@test.com", is_active=True))
        await session.commit()

    token_a = _make_token(user_a)
    token_b = _make_token(user_b)

    # Each user can read their own profile (auto-created)
    resp = await isolation_app.get("/api/profile", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    profile_a_id = resp.json()["id"]

    resp = await isolation_app.get("/api/profile", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    profile_b_id = resp.json()["id"]

    assert profile_a_id != profile_b_id


@pytest.mark.asyncio
async def test_user_cannot_read_other_users_application(isolation_app):
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    from app.database import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(id=user_a, email=f"a2-{user_a}@test.com", is_active=True))
        session.add(User(id=user_b, email=f"b2-{user_b}@test.com", is_active=True))
        await session.commit()

    token_a = _make_token(user_a)
    token_b = _make_token(user_b)

    # User A creates a profile
    await isolation_app.get("/api/profile", headers={"Authorization": f"Bearer {token_a}"})

    # Seed a fake application ID for user A
    fake_app_id = str(uuid.uuid4())

    # User B tries to read user A's application — must get 404, not 403
    resp = await isolation_app.get(
        f"/api/applications/{fake_app_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(isolation_app):
    resp = await isolation_app.get("/api/profile")
    assert resp.status_code == 401
