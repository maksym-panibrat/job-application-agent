"""
E2E regression tests for the job sync/ingestion pipeline.

Covers: job creation, idempotency, timezone roundtrip, and stale job marking.
Tests hit the full FastAPI app backed by a real Postgres container via the
test_app fixture in conftest.py.

These tests were added after discovering that timezone-aware datetimes from
external sources crashed INSERT against naive TIMESTAMP columns.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import select

from app.sources.base import JobData

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_data(
    idx: int = 0,
    posted_at: datetime | None = None,
) -> JobData:
    return JobData(
        external_id=f"sync-e2e-greenhouse-{idx:03d}",
        title=f"Python Engineer #{idx}",
        company_name="Acme Corp",
        location="New York",
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{idx}",
        description_md="We need a Python expert.",
        posted_at=posted_at,
    )


def _mock_greenhouse_source(jobs: list[JobData]) -> MagicMock:
    """Mock GreenhouseBoardSource — returns the given jobs from search()."""
    source = MagicMock()
    source.source_name = "greenhouse_board"
    source.search = AsyncMock(return_value=(jobs, None))
    return source


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_jobs_in_db(test_app, monkeypatch):
    """Synced jobs are persisted and retrievable from the DB."""
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    jobs = [_make_job_data(i) for i in range(3)]
    mock_source = _mock_greenhouse_source(jobs)

    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource", lambda: mock_source
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_jobs"] == 3
    assert data["updated_jobs"] == 0

    # Verify jobs exist in DB with correct fields
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Job).where(Job.source == "greenhouse_board", Job.is_active.is_(True))
        )
        db_jobs = list(result.scalars().all())

    assert len(db_jobs) == 3
    titles = {j.title for j in db_jobs}
    assert titles == {"Python Engineer #0", "Python Engineer #1", "Python Engineer #2"}


@pytest.mark.asyncio
async def test_resync_is_idempotent(test_app, monkeypatch):
    """Re-syncing the same jobs returns new_jobs=0, updated_jobs=3, and the DB still has 3 rows."""
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    jobs = [_make_job_data(i) for i in range(3)]
    mock_source = _mock_greenhouse_source(jobs)

    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource", lambda: mock_source
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    # First sync
    resp1 = await test_app.post("/api/jobs/sync")
    assert resp1.json()["new_jobs"] == 3

    # Second sync with same jobs
    resp2 = await test_app.post("/api/jobs/sync")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["new_jobs"] == 0
    assert data2["updated_jobs"] == 3

    # Confirm no duplicates in DB
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Job).where(Job.source == "greenhouse_board")
        )
        db_jobs = list(result.scalars().all())

    assert len(db_jobs) == 3


@pytest.mark.asyncio
async def test_posted_at_timezone_roundtrip(test_app, monkeypatch):
    """
    Regression: timezone-aware posted_at must survive INSERT and SELECT without error.

    External sources may return datetimes with tzinfo=UTC. Before the timestamptz
    migration, this caused: asyncpg.exceptions.DataError: can't subtract offset-naive
    and offset-aware datetimes on commit.
    """
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    aware_posted_at = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    jobs = [_make_job_data(0, posted_at=aware_posted_at)]
    mock_source = _mock_greenhouse_source(jobs)

    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource", lambda: mock_source
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200
    assert resp.json()["new_jobs"] == 1

    # Read back and assert timezone info is preserved
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Job).where(Job.source == "greenhouse_board")
        )
        job = result.scalar_one()

    assert job.posted_at is not None
    assert job.posted_at.tzinfo is not None
    # Value should round-trip correctly (Postgres timestamptz normalises to UTC)
    assert job.posted_at.replace(tzinfo=UTC) == aware_posted_at


@pytest.mark.asyncio
async def test_stale_job_marking(test_app, monkeypatch):
    """
    Jobs not refreshed within stale_after_days are marked is_active=False on next sync.
    """
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    all_jobs = [_make_job_data(0), _make_job_data(1)]

    # First sync: both jobs appear
    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource",
        lambda: _mock_greenhouse_source(all_jobs),
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.json()["new_jobs"] == 2

    # Backdate job #0's fetched_at so it looks stale
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    stale_job_id: uuid.UUID
    async with factory() as session:
        result = await session.execute(
            select(Job).where(
                Job.source == "greenhouse_board",
                Job.external_id == "sync-e2e-greenhouse-000",
            )
        )
        stale_job = result.scalar_one()
        stale_job.fetched_at = datetime.now(UTC) - timedelta(days=20)
        session.add(stale_job)
        await session.commit()
        stale_job_id = stale_job.id

    # Second sync: only job #1 returned — job #0 is NOT refreshed, so mark_stale picks it up
    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource",
        lambda: _mock_greenhouse_source([all_jobs[1]]),
    )

    resp2 = await test_app.post("/api/jobs/sync")
    assert resp2.status_code == 200
    assert resp2.json()["stale_jobs"] >= 1

    async with factory() as session:
        result = await session.execute(select(Job).where(Job.id == stale_job_id))
        refreshed = result.scalar_one()

    assert refreshed.is_active is False
