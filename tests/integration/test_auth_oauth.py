"""
Integration tests for JWT authentication path.

AUTH_ENABLED=true is set per fixture. A User row is seeded in the test DB.
JWTs are minted with PyJWT using the test secret and the same payload format
that app/api/deps.py decodes.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.models.user import User

_TEST_JWT_SECRET = "test-jwt-secret-is-exactly-32by!"


def _mint_jwt(user_id: uuid.UUID, expired: bool = False) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=(-1 if expired else 86400))
    payload = {
        "sub": str(user_id),
        "aud": ["fastapi-users:auth"],
        "exp": exp,
        "iat": now,
    }
    return pyjwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
async def auth_client(db_session, monkeypatch):
    """
    HTTP client with AUTH_ENABLED=true against the real testcontainers DB.
    Returns (client, seeded_user_id).
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("CRON_SHARED_SECRET", "real-cron-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake-client-secret")

    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)

    from app.main import app

    # Seed a user that JWTs will reference
    user = User(
        id=uuid.uuid4(),
        email="auth-test@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user.id


@pytest.mark.asyncio
async def test_protected_route_without_token_returns_401(auth_client):
    client, _ = auth_client
    response = await client.get("/api/applications")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_valid_jwt_returns_200(auth_client):
    client, user_id = auth_client
    token = _mint_jwt(user_id)
    response = await client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_with_expired_jwt_returns_401(auth_client):
    client, user_id = auth_client
    token = _mint_jwt(user_id, expired=True)
    response = await client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_invalid_jwt_returns_401(auth_client):
    client, _ = auth_client
    response = await client.get(
        "/api/applications", headers={"Authorization": "Bearer not-a-valid-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_unknown_user_id_returns_401(auth_client):
    """A valid JWT whose sub does not exist in the DB returns 401."""
    client, _ = auth_client
    token = _mint_jwt(uuid.uuid4())  # random user_id not in DB
    response = await client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
