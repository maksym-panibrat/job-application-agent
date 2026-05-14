"""
Integration tests for /internal/cron/* endpoints.

These tests verify:
- Each endpoint returns a structured JSON summary (not just {"status": "ok"})
- The summary contains at minimum a status key and a numeric count key
- Invalid secrets are rejected with 403
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401 — registers all SQLModel tables with metadata


@pytest.fixture
async def client(patch_settings, asyncpg_url):
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


CRON_SECRET = "dev-cron-secret"  # matches default SecretStr("dev-cron-secret") in config.py


@pytest.mark.asyncio
async def test_cron_sync_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert isinstance(data["enqueued"], list)
    assert isinstance(data["pruned"], int)
    assert isinstance(data["active_profiles"], int)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/internal/cron/generation-queue",
        "/internal/cron/process-sync-queue",
        "/internal/cron/process-match-queue",
        "/internal/cron/post-cutover-match-reconcile",
    ],
)
async def test_removed_legacy_cron_post_endpoints_do_not_accept_ticks(client, path):
    resp = await client.post(
        path,
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_cron_maintenance_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/maintenance",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert isinstance(data["enqueued"], list)


@pytest.mark.asyncio
async def test_cron_rejects_invalid_secret(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403
