from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings


def make_app(
    secret: str = "test-secret",
    sentry_dsn: str | None = None,
    release: str | None = None,
    raise_server_exceptions: bool = True,
):
    from app.api.internal_cron import get_cron_settings, router

    test_app = FastAPI()
    test_app.include_router(router)

    override_settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        cron_shared_secret=secret,
        google_api_key="fake",
        sentry_dsn=SecretStr(sentry_dsn) if sentry_dsn else None,
        sentry_release=release,
    )
    test_app.dependency_overrides[get_cron_settings] = lambda: override_settings
    return TestClient(test_app, raise_server_exceptions=raise_server_exceptions)


def test_sync_missing_secret_returns_403():
    client = make_app()
    resp = client.post("/internal/cron/sync")
    assert resp.status_code == 403


def test_sync_wrong_secret_returns_403():
    client = make_app(secret="real-secret")
    resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 403


def test_sync_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch(
        "app.api.internal_cron.run_job_sync",
        new=AsyncMock(
            return_value={
                "profiles_synced": 0,
                "total_new_jobs": 0,
                "total_updated_jobs": 0,
                "total_stale_jobs": 0,
            }
        ),
    ) as mock:
        resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()


def test_generation_queue_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch(
        "app.api.internal_cron.run_generation_queue",
        new=AsyncMock(return_value={"attempted": 0, "succeeded": 0, "failed": 0}),
    ) as mock:
        resp = client.post(
            "/internal/cron/generation-queue",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 200
    mock.assert_called_once()


def test_maintenance_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch(
        "app.api.internal_cron.run_daily_maintenance",
        new=AsyncMock(
            return_value={
                "stale_jobs": 0,
                "searches_paused": 0,
                "applications_trimmed": 0,
            }
        ),
    ) as mock:
        resp = client.post("/internal/cron/maintenance", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()


def test_sync_budget_exhausted_returns_structured_response():
    client = make_app(secret="real-secret")
    resumes_at = datetime(2026, 5, 1, tzinfo=UTC)
    with (
        patch(
            "app.api.internal_cron.run_job_sync",
            new=AsyncMock(side_effect=BudgetExhausted(resumes_at)),
        ),
        patch(
            "app.api.internal_cron.sentry_sdk.capture_message",
            return_value="evt1",
        ) as sentry_mock,
    ):
        resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "budget_exhausted"
    assert body["resumes_at"] == resumes_at.isoformat()
    assert isinstance(body["duration_ms"], int)
    sentry_mock.assert_called_once()
    _, kwargs = sentry_mock.call_args
    assert kwargs.get("level") == "warning"


def test_generation_queue_budget_exhausted_returns_structured_response():
    client = make_app(secret="real-secret")
    resumes_at = datetime(2026, 5, 1, tzinfo=UTC)
    with (
        patch(
            "app.api.internal_cron.run_generation_queue",
            new=AsyncMock(side_effect=BudgetExhausted(resumes_at)),
        ),
        patch("app.api.internal_cron.sentry_sdk.capture_message", return_value="evt2"),
    ):
        resp = client.post(
            "/internal/cron/generation-queue",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "budget_exhausted"
    assert body["resumes_at"] == resumes_at.isoformat()


def test_sync_unexpected_exception_returns_500_and_captures_sentry():
    # TestClient with raise_server_exceptions=False mirrors prod behavior: FastAPI
    # converts the unhandled exception into a 500 instead of re-raising into the test.
    client = make_app(secret="real-secret", raise_server_exceptions=False)
    with (
        patch(
            "app.api.internal_cron.run_job_sync",
            new=AsyncMock(side_effect=RuntimeError("db connection refused")),
        ),
        patch(
            "app.api.internal_cron.sentry_sdk.capture_exception",
            return_value="evt3",
        ) as sentry_mock,
    ):
        resp = client.post(
            "/internal/cron/sync",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 500
    sentry_mock.assert_called_once()
    (captured_exc,), _ = sentry_mock.call_args
    assert isinstance(captured_exc, RuntimeError)
    assert "db connection refused" in str(captured_exc)


def test_sentry_ping_missing_secret_returns_403():
    client = make_app(sentry_dsn="https://key@o0.ingest.sentry.io/0")
    resp = client.post("/internal/cron/sentry-ping")
    assert resp.status_code == 403


def test_sentry_ping_no_dsn_returns_not_sent():
    client = make_app(secret="real-secret")
    resp = client.post("/internal/cron/sentry-ping", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False
    assert body["reason"] == "no_dsn_configured"


def test_sentry_ping_captures_message_when_dsn_set():
    client = make_app(
        secret="real-secret",
        sentry_dsn="https://key@o0.ingest.sentry.io/0",
        release="abc123",
    )
    with patch(
        "app.api.internal_cron.sentry_sdk.capture_message",
        return_value="deadbeef",
    ) as mock:
        resp = client.post("/internal/cron/sentry-ping", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is True
    assert body["event_id"] == "deadbeef"
    assert body["release"] == "abc123"
    mock.assert_called_once()
    args, kwargs = mock.call_args
    assert "sentry-ping" in args[0]
    assert kwargs.get("level") == "info"
