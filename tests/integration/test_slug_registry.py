"""Integration tests for slug_fetches model + slug_registry_service."""

import httpx
import pytest
import respx
from sqlmodel import select

from app.models.slug_fetch import SlugFetch
from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


@pytest.mark.asyncio
async def test_slug_fetch_round_trip(db_session):
    row = SlugFetch(source="greenhouse_board", slug="airbnb")
    db_session.add(row)
    await db_session.commit()

    result = await db_session.execute(
        select(SlugFetch).where(
            SlugFetch.source == "greenhouse_board",
            SlugFetch.slug == "airbnb",
        )
    )
    fetched = result.scalar_one()
    assert fetched.is_invalid is False
    assert fetched.consecutive_404_count == 0
    assert fetched.last_fetched_at is None


@pytest.mark.asyncio
async def test_validate_slug_writes_row_on_success(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb").mock(
            return_value=httpx.Response(200, json={"name": "Airbnb", "content": "<p/>"})
        )
        ok = await slug_registry_service.validate_slug("greenhouse_board", "airbnb", db_session)
    assert ok is True
    row = await slug_registry_service.get("greenhouse_board", "airbnb", db_session)
    assert row is not None
    assert row.last_status == "ok"
    assert row.last_fetched_at is None  # validate is existence-only


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404_and_writes_no_row(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai").mock(return_value=httpx.Response(404))
        ok = await slug_registry_service.validate_slug("greenhouse_board", "openai", db_session)
    assert ok is False
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row is None


@pytest.mark.asyncio
async def test_mark_fetched_ok_resets_counters(db_session):
    await slug_registry_service.mark_fetched("greenhouse_board", "stripe", "ok", db_session)
    row = await slug_registry_service.get("greenhouse_board", "stripe", db_session)
    assert row.last_status == "ok"
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 0
    assert row.last_fetched_at is not None
    assert row.queued_at is None
    assert row.claimed_at is None


@pytest.mark.asyncio
async def test_mark_fetched_invalid_increments_404_and_flips_at_2(db_session):
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False  # one strike

    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True  # two strikes — pruned
    assert row.invalid_reason is not None


@pytest.mark.asyncio
async def test_mark_fetched_transient_does_not_count_toward_invalid(db_session):
    for _ in range(5):
        await slug_registry_service.mark_fetched(
            "greenhouse_board", "flaky", "transient_error", db_session
        )
    row = await slug_registry_service.get("greenhouse_board", "flaky", db_session)
    assert row.is_invalid is False
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 5
