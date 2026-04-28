"""
Integration tests for /internal/cron/* endpoints.

These tests verify:
- Each endpoint returns a structured JSON summary (not just {"status": "ok"})
- The summary contains at minimum a status key and a numeric count key
- Invalid secrets are rejected with 403
"""

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
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

    # ASGITransport bypasses the lifespan, so app.state.checkpointer is never
    # initialized. The /internal/cron/generation-queue endpoint 503s without
    # one; seed a MemorySaver to exercise the ok path in tests. Clean up after
    # so subsequent tests (e.g. e2e/test_chat_flow) see a fresh app singleton.
    had_checkpointer = hasattr(app.state, "checkpointer")
    prior_checkpointer = getattr(app.state, "checkpointer", None)
    app.state.checkpointer = MemorySaver()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        if had_checkpointer:
            app.state.checkpointer = prior_checkpointer
        else:
            try:
                del app.state.checkpointer
            except AttributeError:
                pass


CRON_SECRET = "dev-cron-secret"  # matches default SecretStr("dev-cron-secret") in config.py


@pytest.mark.asyncio
async def test_cron_sync_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["profiles_synced"], int)
    assert isinstance(data["duration_ms"], int)
    # profiles_without_slugs is always reported so the operator can see at a glance
    # how many active searches are misconfigured (empty target_company_slugs.greenhouse).
    assert "profiles_without_slugs" in data
    assert isinstance(data["profiles_without_slugs"], int)


@pytest.mark.asyncio
async def test_cron_generation_queue_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/generation-queue",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["attempted"], int)
    assert isinstance(data["duration_ms"], int)


@pytest.mark.asyncio
async def test_cron_maintenance_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/maintenance",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["stale_jobs"], int)
    assert isinstance(data["duration_ms"], int)


@pytest.mark.asyncio
async def test_cron_rejects_invalid_secret(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403
