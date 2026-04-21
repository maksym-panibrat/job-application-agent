"""Unit tests for the /api/applications/{id}/interest endpoint."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.user_profile import UserProfile


def _make_test_app():
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("GOOGLE_API_KEY", "fake")
    from app.api.applications import router
    from app.api.deps import get_current_profile, get_db

    app = FastAPI()
    app.include_router(router)

    profile_id = uuid.uuid4()
    mock_profile = MagicMock(spec=UserProfile)
    mock_profile.id = profile_id

    app.dependency_overrides[get_current_profile] = lambda: mock_profile
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    return app, profile_id


def test_invalid_interest_value_returns_422():
    app, profile_id = _make_test_app()
    client = TestClient(app)
    app_id = uuid.uuid4()

    with patch("app.api.applications.get_db"):
        resp = client.patch(f"/api/applications/{app_id}/interest", json={"interest": "maybe"})
    assert resp.status_code == 422
