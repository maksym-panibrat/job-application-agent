"""Tests for run_sync_queue dispatching via SOURCES per row.source."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.models.slug_fetch import SlugFetch
from app.scheduler.tasks import run_sync_queue


@pytest.mark.asyncio
async def test_run_sync_queue_dispatches_per_provider(db_session):
    """Each claimed SlugFetch is fed to the matching adapter in SOURCES,
    keyed by row.source."""
    fake_greenhouse = AsyncMock()
    fake_greenhouse.fetch_jobs = AsyncMock(return_value=[])
    fake_lever = AsyncMock()
    fake_lever.fetch_jobs = AsyncMock(return_value=[])

    # Seed two queued SlugFetches, one per provider.
    db_session.add(SlugFetch(source="greenhouse", slug="stripe", queued_at=datetime.now(UTC)))
    db_session.add(SlugFetch(source="lever", slug="acme", queued_at=datetime.now(UTC)))
    await db_session.commit()

    with patch.dict(
        "app.sources.SOURCES",
        {"greenhouse": fake_greenhouse, "lever": fake_lever},
        clear=False,
    ):
        await run_sync_queue(deadline_seconds=5, max_slugs=10)

    fake_greenhouse.fetch_jobs.assert_awaited()
    fake_lever.fetch_jobs.assert_awaited()


@pytest.mark.asyncio
async def test_run_sync_queue_unknown_provider_marks_transient_error(db_session):
    """A SlugFetch with an unknown source (e.g., legacy 'myspace') gets
    marked transient_error and counted, not crashed-on."""
    db_session.add(SlugFetch(source="myspace", slug="acme", queued_at=datetime.now(UTC)))
    await db_session.commit()

    result = await run_sync_queue(deadline_seconds=5, max_slugs=10)
    assert result["transient"] >= 1

    # The row should have been marked, not still queued.
    db_session.expire_all()
    row = (
        await db_session.execute(
            select(SlugFetch).where(SlugFetch.source == "myspace", SlugFetch.slug == "acme")
        )
    ).scalar_one()
    assert row.last_status == "transient_error"
