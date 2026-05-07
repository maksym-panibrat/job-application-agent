"""Integration tests for POST /api/events — the analytics ingest endpoint
defined in Plan D. Validates batching, capping, and the 204
fire-and-forget contract."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_events_post_returns_204_for_authenticated_batch(
    db_session, auth_headers, seeded_user
):
    """Authenticated client posts a batch; rows land tied to profile_id."""
    # Deferred import: app/main.py calls get_settings() at import time; the
    # patch_settings autouse fixture sets DATABASE_URL before test body runs.
    from app.main import app as fastapi_app

    body = {
        "session_id": "sess-abc",
        "events": [
            {
                "name": "feed.viewed",
                "properties": {"status_filter": "pending"},
                "path": "/",
            },
            {
                "name": "match.card_opened",
                "properties": {"application_id": "x", "score": 0.87},
                "path": "/",
            },
        ],
    }
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        r = await client.post("/api/events", json=body, headers=auth_headers)
    assert r.status_code == 204

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.event import Event

    async with get_session_factory()() as s:
        rows = (
            (await s.execute(select(Event).where(Event.session_id == "sess-abc"))).scalars().all()
        )
    assert len(rows) == 2
    names = {row.name for row in rows}
    assert names == {"feed.viewed", "match.card_opened"}
    assert all(row.profile_id is not None for row in rows)


@pytest.mark.asyncio
async def test_events_caps_batch_at_50(db_session, auth_headers, seeded_user):
    """A batch of 60 events ingests only the first 50; overflow is silently dropped."""
    from app.main import app as fastapi_app

    body = {
        "session_id": "sess-cap",
        "events": [
            {"name": f"test.event_{i}", "properties": None, "path": None} for i in range(60)
        ],
    }
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        r = await client.post("/api/events", json=body, headers=auth_headers)
    assert r.status_code == 204

    from sqlmodel import func, select

    from app.database import get_session_factory
    from app.models.event import Event

    async with get_session_factory()() as s:
        cnt = (
            await s.execute(
                select(func.count()).select_from(Event).where(Event.session_id == "sess-cap")
            )
        ).scalar_one()
    assert cnt == 50, f"expected 50 rows after cap, got {cnt}"


@pytest.mark.asyncio
async def test_events_records_user_agent_and_path(db_session, auth_headers, seeded_user):
    """The endpoint extracts UA from request headers and path from each event."""
    from app.main import app as fastapi_app

    body = {
        "session_id": "sess-ua",
        "events": [{"name": "feed.viewed", "properties": None, "path": "/?status=applied"}],
    }
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/api/events",
            json=body,
            headers={**auth_headers, "User-Agent": "TestAgent/1.0"},
        )
    assert r.status_code == 204

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.event import Event

    async with get_session_factory()() as s:
        row = (await s.execute(select(Event).where(Event.session_id == "sess-ua"))).scalar_one()
    assert "TestAgent" in (row.user_agent or "")
    assert row.path == "/?status=applied"
