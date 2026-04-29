"""Integration tests for POST /api/jobs/sync.

The new contract returns 202 Accepted with {status: 'queued', queued_slugs, matched_now}
instead of 200 with synchronous results. Background fetch + match catches up via cron.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401


@pytest.fixture
async def client(patch_settings, asyncpg_url):
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_sync_endpoint_returns_202(client, auth_headers):
    """New contract: POST /api/jobs/sync returns 202 with the queued summary,
    not 200 with synchronous results."""
    response = await client.post("/api/jobs/sync", headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert "queued_slugs" in body
    assert "matched_now" in body
