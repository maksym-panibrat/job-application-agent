from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings


def make_app(secret: str = "test-secret"):
    from app.api.internal_cron import get_cron_settings, router

    test_app = FastAPI()
    test_app.include_router(router)

    override_settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        cron_shared_secret=secret,
        google_api_key="fake",
    )
    test_app.dependency_overrides[get_cron_settings] = lambda: override_settings
    return TestClient(test_app)


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
        new=AsyncMock(return_value={
            "profiles_synced": 0, "total_new_jobs": 0,
            "total_updated_jobs": 0, "total_stale_jobs": 0,
        }),
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
        new=AsyncMock(return_value={
            "stale_jobs": 0, "searches_paused": 0, "applications_trimmed": 0,
        }),
    ) as mock:
        resp = client.post("/internal/cron/maintenance", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()
