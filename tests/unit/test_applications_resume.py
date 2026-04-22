"""Unit tests for POST /api/applications/{app_id}/resume."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.user_profile import UserProfile


@pytest.fixture(autouse=True)
def _noop_background_resume():
    """Replace the DB-hitting background task with a no-op.

    FastAPI's TestClient runs BackgroundTasks inline after the request returns.
    The real _resume_in_background opens a fresh SQLAlchemy session, which
    fails in unit tests (no DB available); we only care about the HTTP
    response-shape contract here.
    """
    with patch(
        "app.api.applications._resume_in_background",
        new=AsyncMock(return_value=None),
    ):
        yield


def _make_test_app(
    *,
    app_row=None,
    profile_id: uuid.UUID | None = None,
    checkpointer: object | None = object(),
) -> tuple[FastAPI, uuid.UUID]:
    """Build a FastAPI test app with stubbed deps.

    ``app_row`` is returned from ``session.get(Application, ...)``. If None,
    the session returns None (simulating a 404).
    """
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("GOOGLE_API_KEY", "fake")
    from app.api.applications import router
    from app.api.deps import get_current_profile
    from app.database import get_db

    app = FastAPI()
    app.include_router(router)

    pid = profile_id or uuid.uuid4()
    mock_profile = MagicMock(spec=UserProfile)
    mock_profile.id = pid

    session = AsyncMock()
    session.get = AsyncMock(return_value=app_row)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    # Simulate the atomic UPDATE ... RETURNING used by the resume endpoint.
    # The conditional UPDATE returns a row only when the pre-conditions
    # (status = 'awaiting_review', and for regenerate generation_attempts < 3)
    # are satisfied on the mocked Application row. We inspect the SQL text and
    # parameters to decide whether the update "matched" a row.
    def _execute_side_effect(stmt, params=None):
        sql = str(stmt).lower()
        result = MagicMock()
        is_update = "update applications" in sql
        if not is_update or app_row is None:
            result.fetchone = MagicMock(return_value=None)
            return result

        is_regenerate = "generation_attempts = generation_attempts + 1" in sql
        status_ok = app_row.generation_status == "awaiting_review"
        attempts_ok = (not is_regenerate) or app_row.generation_attempts < 3
        if status_ok and attempts_ok:
            if is_regenerate:
                app_row.generation_attempts += 1
            app_row.generation_status = "generating"
            row_value = MagicMock()
            row_value.__getitem__ = lambda _self, _k: app_row.generation_attempts
            result.fetchone = MagicMock(return_value=row_value)
        else:
            result.fetchone = MagicMock(return_value=None)
        return result

    session.execute = AsyncMock(side_effect=_execute_side_effect)

    async def _get_db_override():
        yield session

    app.dependency_overrides[get_current_profile] = lambda: mock_profile
    app.dependency_overrides[get_db] = _get_db_override

    if checkpointer is not None:
        app.state.checkpointer = checkpointer

    return app, pid


def _mock_application(
    *,
    profile_id: uuid.UUID,
    generation_status: str = "awaiting_review",
    generation_attempts: int = 1,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.profile_id = profile_id
    row.generation_status = generation_status
    row.generation_attempts = generation_attempts
    return row


def test_resume_approve_happy_path_returns_200():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="awaiting_review")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(app_row.id)
    assert body["generation_status"] == "generating"
    assert body["decision"] == "approve"


def test_resume_regenerate_happy_path_returns_200():
    pid = uuid.uuid4()
    app_row = _mock_application(
        profile_id=pid, generation_status="awaiting_review", generation_attempts=1
    )
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "regenerate"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["decision"] == "regenerate"


def test_resume_wrong_profile_returns_404():
    # Application belongs to a different profile than the authenticated one.
    app_row = _mock_application(profile_id=uuid.uuid4(), generation_status="awaiting_review")
    # Use a fresh random profile_id in the app (won't match app_row.profile_id)
    app, _ = _make_test_app(app_row=app_row)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 404


def test_resume_missing_application_returns_404():
    app, _ = _make_test_app(app_row=None)
    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{uuid.uuid4()}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 404


def test_resume_wrong_status_ready_returns_409():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="ready")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 409


def test_resume_wrong_status_failed_returns_409():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="failed")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 409


def test_resume_regenerate_at_max_attempts_returns_429():
    pid = uuid.uuid4()
    app_row = _mock_application(
        profile_id=pid, generation_status="awaiting_review", generation_attempts=3
    )
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "regenerate"},
    )
    assert resp.status_code == 429


def test_resume_approve_at_max_attempts_still_allowed():
    # Approve does not consume a regeneration attempt — the 3-attempt cap only
    # matters for the regenerate path.
    pid = uuid.uuid4()
    app_row = _mock_application(
        profile_id=pid, generation_status="awaiting_review", generation_attempts=3
    )
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 200


def test_resume_invalid_decision_returns_422():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="awaiting_review")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "maybe"},
    )
    assert resp.status_code == 422


def test_resume_missing_decision_returns_422():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="awaiting_review")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={},
    )
    assert resp.status_code == 422


def test_resume_no_checkpointer_returns_503():
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="awaiting_review")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid, checkpointer=None)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "checkpointer not initialized"


def test_resume_generating_returns_409():
    """Status=generating already (e.g. a resume is in flight) — reject."""
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="generating")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 409


def test_resume_pending_returns_409():
    """Status=pending (initial queued state) — resume not valid yet."""
    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="pending")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    client = TestClient(app)
    resp = client.post(
        f"/api/applications/{app_row.id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 409


def test_resume_approve_schedules_mapped_payload():
    """Happy path: the mapped Command payload reaches _resume_in_background.

    The endpoint must translate the public ``"approve"`` string into the
    LangGraph ``Command(resume={"approved": True})`` payload before scheduling
    the background task — never pass the raw string through.
    """
    from app.api import applications as applications_mod

    pid = uuid.uuid4()
    app_row = _mock_application(profile_id=pid, generation_status="awaiting_review")
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    with patch.object(
        applications_mod, "_resume_in_background", new=AsyncMock(return_value=None)
    ) as spy:
        client = TestClient(app)
        resp = client.post(
            f"/api/applications/{app_row.id}/resume",
            json={"decision": "approve"},
        )
        assert resp.status_code == 200
        # Positional args: (app_id, command_payload, checkpointer)
        assert spy.await_count == 1
        _, payload, _ = spy.await_args.args
        assert payload == {"approved": True}


def test_resume_regenerate_schedules_mapped_payload():
    """Happy path: regenerate string maps to ``{"regenerate": True}``."""
    from app.api import applications as applications_mod

    pid = uuid.uuid4()
    app_row = _mock_application(
        profile_id=pid, generation_status="awaiting_review", generation_attempts=1
    )
    app, _ = _make_test_app(app_row=app_row, profile_id=pid)

    with patch.object(
        applications_mod, "_resume_in_background", new=AsyncMock(return_value=None)
    ) as spy:
        client = TestClient(app)
        resp = client.post(
            f"/api/applications/{app_row.id}/resume",
            json={"decision": "regenerate"},
        )
        assert resp.status_code == 200
        assert spy.await_count == 1
        _, payload, _ = spy.await_args.args
        assert payload == {"regenerate": True}
