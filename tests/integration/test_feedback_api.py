from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient


class _FakeWebhookClient:
    def __init__(self, *, calls, response=None, error=None, **kwargs):
        self.calls = calls
        self.response = response
        self.error = error
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json):
        self.calls.append({"url": url, "json": json})
        if self.error is not None:
            raise self.error
        return self.response


def _configure_feedback_webhook(monkeypatch):
    monkeypatch.setenv("FEEDBACK_WEBHOOK_URL", "https://example.test/feedback")
    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)


def _webhook_body(message="Broken page"):
    return {
        "category": "bug",
        "message": message,
        "diagnostics": {
            "reported_at_client": "2026-05-25T20:15:00.000Z",
            "path": "/matches/abc",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 1440, "height": 900},
            "timezone": "America/Los_Angeles",
            "route_context": {"application_id": "abc"},
        },
    }


@pytest.mark.asyncio
async def test_feedback_webhook_success_records_sent(
    db_session, auth_headers, seeded_user, monkeypatch
):
    _configure_feedback_webhook(monkeypatch)
    import app.services.feedback_service as feedback_service
    from app.main import app as fastapi_app

    webhook_calls = []
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeWebhookClient(
            calls=webhook_calls,
            response=SimpleNamespace(status_code=204),
            **kwargs,
        ),
    )

    body = _webhook_body()
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["notification_status"] == "sent"

    assert len(webhook_calls) == 1
    assert webhook_calls[0]["url"] == "https://example.test/feedback"
    webhook_payload = webhook_calls[0]["json"]
    assert webhook_payload["event"] == "feedback.submitted"
    assert webhook_payload["feedback_id"] == payload["id"]
    assert webhook_payload["path"] == "/matches/abc"
    assert webhook_payload["message_preview"] == "Broken page"
    assert "message" not in webhook_payload

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(FeedbackReport.id == UUID(payload["id"]))
            )
        ).scalar_one()

    assert row.notification_status == "sent"
    assert row.notification_error is None


@pytest.mark.asyncio
async def test_feedback_webhook_failure_records_failed(
    db_session, auth_headers, seeded_user, monkeypatch
):
    _configure_feedback_webhook(monkeypatch)
    import app.services.feedback_service as feedback_service
    from app.main import app as fastapi_app

    webhook_calls = []
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeWebhookClient(
            calls=webhook_calls,
            error=RuntimeError("receiver down"),
            **kwargs,
        ),
    )

    body = _webhook_body()
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["notification_status"] == "failed"

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(FeedbackReport.id == UUID(payload["id"]))
            )
        ).scalar_one()

    assert row.notification_status == "failed"
    assert row.notification_error is not None
    assert "receiver down" in row.notification_error


@pytest.mark.asyncio
async def test_feedback_submit_creates_row(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    body = {
        "category": "feature_request",
        "message": "Please let me hide companies I rejected.",
        "diagnostics": {
            "reported_at_client": "2026-05-25T20:15:00.000Z",
            "path": "/matches?status=pending",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 1440, "height": 900},
            "timezone": "America/Los_Angeles",
            "route_context": {},
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] is True
    assert payload["notification_status"] == "not_configured"

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    user, _profile = seeded_user
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(
                    FeedbackReport.user_id == user.id,
                    FeedbackReport.message == body["message"],
                )
            )
        ).scalar_one()

    assert row.user_email == user.email
    assert row.category == "feature_request"
    assert row.message == "Please let me hide companies I rejected."
    assert row.notification_status == "not_configured"
    assert row.notification_error is None
    assert row.diagnostics["path"] == "/matches?status=pending"


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_category(auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "confusing", "message": "bad category", "diagnostics": {}},
            headers=auth_headers,
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_feedback_rejects_empty_message(auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "   ", "diagnostics": {}},
            headers=auth_headers,
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_feedback_requires_auth():
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "Cannot submit", "diagnostics": {}},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_feedback_sanitizes_diagnostics(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    body = {
        "category": "bug",
        "message": "The match page looked wrong.",
        "diagnostics": {
            "reported_at_client": "x" * 100,
            "path": "/matches/abc?chat=1",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 390, "height": 844, "ignored": "drop me"},
            "timezone": "America/Los_Angeles",
            "route_context": {"application_id": "abc", "too_long": "y" * 300},
            "page_content": "must be dropped",
        },
    }
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    feedback_id = response.json()["id"]

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(FeedbackReport.id == UUID(feedback_id))
            )
        ).scalar_one()

    assert set(row.diagnostics) == {
        "reported_at_client",
        "path",
        "page_title",
        "user_agent",
        "viewport",
        "timezone",
        "route_context",
    }
    assert len(row.diagnostics["reported_at_client"]) == 64
    assert row.diagnostics["viewport"] == {"width": 390, "height": 844}
    assert row.diagnostics["route_context"]["application_id"] == "abc"
    assert len(row.diagnostics["route_context"]["too_long"]) == 128
