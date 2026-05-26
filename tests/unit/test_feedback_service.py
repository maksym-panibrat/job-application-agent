from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

import app.services.feedback_service as feedback_service
from app.models.feedback_report import FeedbackReport
from app.services.feedback_service import (
    STATUS_FAILED,
    STATUS_NOT_CONFIGURED,
    STATUS_SENT,
    FeedbackValidationError,
    build_webhook_payload,
    create_feedback_report,
    dispatch_feedback_webhook,
    sanitize_diagnostics,
    validate_message,
)


def _feedback_report(message: str = "hello") -> FeedbackReport:
    return FeedbackReport(
        id=uuid4(),
        user_id=uuid4(),
        user_email="user@example.com",
        category="bug",
        message=message,
        diagnostics={"path": "/matches/abc", "timezone": "America/Los_Angeles"},
        notification_status="pending",
        created_at=datetime(2026, 5, 25, 20, 15, tzinfo=UTC),
    )


def test_sanitize_diagnostics_drops_boolean_viewport_dimensions():
    diagnostics = sanitize_diagnostics(
        {
            "path": "/matches",
            "viewport": {"width": True, "height": False},
            "timezone": "America/Los_Angeles",
        }
    )

    assert diagnostics == {
        "path": "/matches",
        "timezone": "America/Los_Angeles",
    }


def test_build_webhook_payload_matches_feedback_submission_contract():
    report = _feedback_report(message="x" * 300)

    payload = build_webhook_payload(report)

    assert payload["event"] == "feedback.submitted"
    assert payload["feedback_id"] == str(report.id)
    assert "id" not in payload
    assert "message" not in payload
    assert payload["message_preview"] == "x" * 240
    assert payload["path"] == "/matches/abc"
    assert payload["diagnostics"] == report.diagnostics


class _FakeAsyncClient:
    def __init__(self, response=None, error=None, **kwargs):
        self.response = response
        self.error = error
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json):
        if self.error:
            raise self.error
        return self.response


class _CaptureSession:
    def __init__(
        self, *, fail_second_commit: bool = False, fail_rollback: bool = False
    ):
        self.report = None
        self.commit_count = 0
        self.fail_second_commit = fail_second_commit
        self.fail_rollback = fail_rollback

    def add(self, report):
        self.report = report

    async def commit(self):
        self.commit_count += 1
        if self.fail_second_commit and self.commit_count == 2:
            raise RuntimeError("status update failed")

    async def refresh(self, report):
        return None

    async def rollback(self):
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        self.report.id = None
        self.report.notification_status = "expired"


@pytest.mark.asyncio
async def test_dispatch_feedback_webhook_logs_success(monkeypatch):
    report = _feedback_report()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    fake_log = SimpleNamespace(ainfo=AsyncMock(), aerror=AsyncMock())
    monkeypatch.setattr(feedback_service, "log", fake_log)
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=204), **kwargs),
    )

    await dispatch_feedback_webhook(report, session=session, settings=settings)

    assert report.notification_status == STATUS_SENT
    assert report.notification_error is None
    fake_log.ainfo.assert_awaited_once()
    assert fake_log.ainfo.await_args.args == ("feedback.notification_sent",)


@pytest.mark.asyncio
async def test_dispatch_feedback_webhook_logs_failure(monkeypatch):
    report = _feedback_report()
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    fake_log = SimpleNamespace(ainfo=AsyncMock(), aerror=AsyncMock())
    monkeypatch.setattr(feedback_service, "log", fake_log)
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=500), **kwargs),
    )

    await dispatch_feedback_webhook(report, session=session, settings=settings)

    assert report.notification_status == STATUS_FAILED
    assert report.notification_error == "Webhook returned HTTP 500"
    fake_log.aerror.assert_awaited_once()
    assert fake_log.aerror.await_args.args == ("feedback.notification_failed",)


@pytest.mark.asyncio
async def test_create_feedback_report_returns_stable_fields_after_status_commit_rollback(
    monkeypatch,
):
    user = SimpleNamespace(id=uuid4(), email="user@example.com")
    session = _CaptureSession(fail_second_commit=True)
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=204), **kwargs),
    )

    result = await create_feedback_report(
        user=user,
        category="bug",
        message="A useful report",
        diagnostics={"path": "/matches/abc"},
        session=session,
        settings=settings,
    )

    assert result.id is not None
    assert result.notification_status == STATUS_SENT
    assert session.report.id is None
    assert session.report.notification_status == "expired"


@pytest.mark.asyncio
async def test_create_feedback_report_returns_after_status_commit_and_rollback_fail(
    monkeypatch,
):
    user = SimpleNamespace(id=uuid4(), email="user@example.com")
    session = _CaptureSession(fail_second_commit=True, fail_rollback=True)
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=204), **kwargs),
    )

    result = await create_feedback_report(
        user=user,
        category="bug",
        message="A useful report",
        diagnostics={"path": "/matches/abc"},
        session=session,
        settings=settings,
    )

    assert result.id is not None
    assert result.notification_status == STATUS_SENT


@pytest.mark.asyncio
async def test_create_feedback_report_returns_after_status_commit_and_failure_log_fail(
    monkeypatch,
):
    user = SimpleNamespace(id=uuid4(), email="user@example.com")
    session = _CaptureSession(fail_second_commit=True)
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    fake_log = SimpleNamespace(
        ainfo=AsyncMock(),
        aerror=AsyncMock(side_effect=RuntimeError("log failed")),
    )
    monkeypatch.setattr(feedback_service, "log", fake_log)
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=204), **kwargs),
    )

    result = await create_feedback_report(
        user=user,
        category="bug",
        message="A useful report",
        diagnostics={"path": "/matches/abc"},
        session=session,
        settings=settings,
    )

    assert result.id is not None
    assert result.notification_status == STATUS_SENT


@pytest.mark.asyncio
async def test_create_feedback_report_returns_after_notification_success_log_fail(
    monkeypatch,
):
    user = SimpleNamespace(id=uuid4(), email="user@example.com")
    session = _CaptureSession()
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("https://example.test/webhook"),
        feedback_webhook_timeout_seconds=3.0,
    )
    fake_log = SimpleNamespace(
        ainfo=AsyncMock(side_effect=[None, RuntimeError("log failed")]),
        aerror=AsyncMock(),
    )
    monkeypatch.setattr(feedback_service, "log", fake_log)
    monkeypatch.setattr(
        feedback_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeAsyncClient(response=SimpleNamespace(status_code=204), **kwargs),
    )

    result = await create_feedback_report(
        user=user,
        category="bug",
        message="A useful report",
        diagnostics={"path": "/matches/abc"},
        session=session,
        settings=settings,
    )

    assert result.id is not None
    assert result.notification_status == STATUS_SENT


@pytest.mark.asyncio
async def test_create_feedback_report_treats_blank_webhook_secret_as_not_configured():
    user = SimpleNamespace(id=uuid4(), email="user@example.com")
    session = _CaptureSession()
    settings = SimpleNamespace(
        feedback_webhook_url=SecretStr("   "),
        feedback_webhook_timeout_seconds=3.0,
    )

    result = await create_feedback_report(
        user=user,
        category="bug",
        message="A useful report",
        diagnostics={"path": "/matches/abc"},
        session=session,
        settings=settings,
    )

    assert result.notification_status == STATUS_NOT_CONFIGURED
    assert session.commit_count == 1


def test_validate_message_rejects_over_limit_message():
    with pytest.raises(FeedbackValidationError):
        validate_message("x" * 5001)


def test_sanitize_diagnostics_rejects_non_object_diagnostics():
    with pytest.raises(FeedbackValidationError):
        sanitize_diagnostics(["path", "/matches"])


def test_sanitize_diagnostics_rejects_oversized_sanitized_diagnostics():
    diagnostics = {
        "route_context": {
            f"k{i:02d}".ljust(128, "k"): "x" * 128 for i in range(64)
        },
        "path": "x" * 512,
        "page_title": "x" * 256,
        "user_agent": "x" * 512,
        "timezone": "x" * 128,
        "reported_at_client": "x" * 64,
    }

    with pytest.raises(FeedbackValidationError):
        sanitize_diagnostics(diagnostics)


def test_sanitize_diagnostics_caps_route_context_to_64_entries():
    diagnostics = sanitize_diagnostics(
        {"route_context": {f"k{i}": f"v{i}" for i in range(70)}}
    )

    assert len(diagnostics["route_context"]) == 64
    assert "k63" in diagnostics["route_context"]
    assert "k64" not in diagnostics["route_context"]
