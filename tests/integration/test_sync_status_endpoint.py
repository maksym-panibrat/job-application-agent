"""GET /api/sync/status — used by the dashboard chip to poll progress."""

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401
from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.services import slug_registry_service
from app.worker.payloads import FetchSlugPayload
from app.worker.queue_service import enqueue


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
    company = Company(
        canonical_name="Airbnb",
        normalized_key="airbnb",
        provider_slugs={"greenhouse": "airbnb"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()
    await enqueue(
        db_session,
        job_type="fetch-slug",
        payload=FetchSlugPayload(provider="greenhouse", slug="airbnb").model_dump(),
        dedupe_key="fetch-slug:greenhouse:airbnb",
    )
    await db_session.commit()

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["state"] == "syncing"
    assert body["slugs_pending"] == 1


@pytest.mark.asyncio
async def test_status_lists_invalid_slugs(client, auth_headers, seeded_user, db_session):
    _, profile = seeded_user
    company = Company(
        canonical_name="OpenAI",
        normalized_key="openai",
        provider_slugs={"greenhouse": "openai"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["invalid_slugs"] == ["openai"]


@pytest.mark.asyncio
async def test_status_reconciles_stale_queued_summary_when_idle(
    client, auth_headers, seeded_user, db_session
):
    _, profile = seeded_user
    company = Company(
        canonical_name="Anthropic",
        normalized_key="anthropic",
        provider_slugs={"greenhouse": "anthropic"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile.target_company_ids = [company.id]
    profile.last_sync_requested_at = datetime(2026, 5, 14, 4, 0, tzinfo=UTC)
    profile.last_sync_completed_at = datetime(2026, 5, 13, 22, 45, tzinfo=UTC)
    profile.last_sync_summary = {
        "queued_slugs": ["anthropic"],
        "matched_now": 0,
        "pruned_slugs": 0,
    }
    db_session.add(profile)
    db_session.add(
        SlugFetch(
            source="greenhouse",
            slug="anthropic",
            last_fetched_at=datetime(2026, 5, 14, 4, 0, 20, tzinfo=UTC),
        )
    )
    await db_session.commit()

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()

    assert body["state"] == "idle"
    assert body["last_sync_summary"]["queued_slugs"] == []
    assert body["last_sync_completed_at"] >= "2026-05-14T04:00:00"
