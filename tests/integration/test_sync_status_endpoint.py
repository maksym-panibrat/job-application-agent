"""GET /api/sync/status — used by the dashboard chip to poll progress."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401
from app.services import slug_registry_service


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
async def test_status_idle_when_nothing_queued(client, auth_headers, seeded_user):
    response = await client.get("/api/sync/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["slugs_pending"] == 0
    assert body["matches_pending"] == 0
    assert body["invalid_slugs"] == []


@pytest.mark.asyncio
async def test_status_syncing_when_user_slug_queued(client, auth_headers, seeded_user, db_session):
    _, profile = seeded_user
    profile.target_company_slugs = {"greenhouse": ["airbnb"]}
    db_session.add(profile)
    await db_session.commit()
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["state"] == "syncing"
    assert body["slugs_pending"] == 1


@pytest.mark.asyncio
async def test_status_lists_invalid_slugs(client, auth_headers, seeded_user, db_session):
    _, profile = seeded_user
    profile.target_company_slugs = {"greenhouse": ["openai"]}
    db_session.add(profile)
    await db_session.commit()
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["invalid_slugs"] == ["openai"]
