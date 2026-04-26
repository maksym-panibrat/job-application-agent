"""
Unit tests for get_current_user in app/api/deps.py.

Approach:
- Each test builds a minimal FastAPI app with get_current_user mounted on a
  dummy endpoint so the full Depends() chain runs through TestClient.
- The DB session is overridden with a lightweight AsyncMock that returns
  pre-staged User objects (or None), keeping these tests at unit tier
  (no testcontainers, no real Postgres).
- structlog.testing.capture_logs() is used to assert server-side log events
  without touching stdlib logging handlers.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User

# The JWT secret used across all tests that craft valid or intentionally
# wrong-secret tokens.
TEST_SECRET = "unit-test-jwt-secret-that-is-32-bytes-long!!"
WRONG_SECRET = "wrong-secret-that-is-also-32-bytes-long!!!"

# Audience claim that deps.py expects.
AUDIENCE = ["fastapi-users:auth"]

# A stable UUID for a fictional active user.
ACTIVE_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str | None = str(ACTIVE_USER_ID),
    secret: str = TEST_SECRET,
    exp_delta: timedelta = timedelta(hours=1),
    aud: list[str] = AUDIENCE,
    include_exp: bool = True,
) -> str:
    """Craft a HS256 JWT with the same claim shape as make_smoke_token.py."""
    now = datetime.now(UTC)
    payload: dict = {"aud": aud, "iat": int(now.timestamp())}
    if sub is not None:
        payload["sub"] = sub
    if include_exp:
        payload["exp"] = int((now + exp_delta).timestamp())
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        jwt_secret=TEST_SECRET,
        google_api_key="fake",
    )


def _make_session_mock(returned_user: User | None) -> AsyncMock:
    """Return an AsyncMock that satisfies session.get(User, id) → returned_user."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=returned_user)
    return session


def _make_app(session_mock: AsyncMock | None = None) -> TestClient:
    """
    Build a minimal FastAPI app with a single GET /me endpoint that exercises
    get_current_user, wiring in overrides for get_db and get_settings.
    """
    app = FastAPI()
    settings = _make_settings()

    @app.get("/me")
    async def me(user: User = __import__("fastapi").Depends(get_current_user)):
        return {"id": str(user.id), "email": user.email}

    if session_mock is not None:
        app.dependency_overrides[get_db] = lambda: session_mock
    app.dependency_overrides[get_settings] = lambda: settings

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Expired token → auth.token_expired
# ---------------------------------------------------------------------------


def test_get_current_user_token_expired():
    token = _make_token(exp_delta=timedelta(seconds=-60))  # already expired
    session = _make_session_mock(None)
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any(e["event"] == "auth.token_expired" for e in captured), captured


# ---------------------------------------------------------------------------
# 2. Wrong signature → auth.token_invalid_signature
# ---------------------------------------------------------------------------


def test_get_current_user_token_invalid_signature():
    token = _make_token(secret=WRONG_SECRET)
    session = _make_session_mock(None)
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any(e["event"] == "auth.token_invalid_signature" for e in captured), captured


# ---------------------------------------------------------------------------
# 3. Missing sub claim → auth.token_missing_sub
# ---------------------------------------------------------------------------


def test_get_current_user_token_missing_sub():
    token = _make_token(sub=None)  # omit sub
    session = _make_session_mock(None)
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any(e["event"] == "auth.token_missing_sub" for e in captured), captured


# ---------------------------------------------------------------------------
# 4. Valid token but user not in DB → auth.user_not_found
# ---------------------------------------------------------------------------


def test_get_current_user_user_not_found():
    unknown_id = uuid.uuid4()
    token = _make_token(sub=str(unknown_id))
    session = _make_session_mock(returned_user=None)  # DB returns nothing
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any(e["event"] == "auth.user_not_found" for e in captured), captured


# ---------------------------------------------------------------------------
# 5. Valid token but user.is_active=False → auth.user_inactive
# ---------------------------------------------------------------------------


def test_get_current_user_inactive_user():
    inactive = User(
        id=ACTIVE_USER_ID,
        email="inactive@example.com",
        is_active=False,
        hashed_password="",
    )
    token = _make_token(sub=str(ACTIVE_USER_ID))
    session = _make_session_mock(returned_user=inactive)
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert any(e["event"] == "auth.user_inactive" for e in captured), captured


# ---------------------------------------------------------------------------
# 6. Happy path — valid token, active user → 200
# ---------------------------------------------------------------------------


def test_get_current_user_happy_path():
    active = User(
        id=ACTIVE_USER_ID,
        email="active@example.com",
        is_active=True,
        hashed_password="",
    )
    token = _make_token(sub=str(ACTIVE_USER_ID))
    session = _make_session_mock(returned_user=active)
    client = _make_app(session_mock=session)

    with capture_logs() as captured:
        resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(ACTIVE_USER_ID)
    # No auth-error log events should appear on the happy path.
    auth_errors = [e for e in captured if e.get("event", "").startswith("auth.")]
    assert auth_errors == [], auth_errors


# ---------------------------------------------------------------------------
# 7. No Authorization header → always 401 (no bypass path exists)
# ---------------------------------------------------------------------------


def test_get_current_user_no_token_returns_401():
    session = _make_session_mock(None)
    client = _make_app(session_mock=session)

    resp = client.get("/me")  # no Authorization header

    assert resp.status_code == 401
