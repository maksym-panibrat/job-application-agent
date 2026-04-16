"""
E2E regression tests for the job sync/ingestion pipeline.

Covers: job creation, idempotency, timezone roundtrip, multi-source,
and stale job marking. Tests hit the full FastAPI app backed by a real
Postgres container via the test_app fixture in conftest.py.

These tests were added after discovering that JSearch produced timezone-aware
datetimes that crashed INSERT against naive TIMESTAMP columns.
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
    source_name: str = "adzuna",
    posted_at: datetime | None = None,
) -> JobData:
    return JobData(
        external_id=f"sync-e2e-{source_name}-{idx:03d}",
        title=f"Python Engineer #{idx}",
        company_name="Acme Corp",
        location="New York",
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{idx}",
        ats_type="greenhouse",
        supports_api_apply=True,
        description_md="We need a Python expert.",
        posted_at=posted_at,
    )


def _mock_source(name: str, jobs: list[JobData]) -> MagicMock:
    source = MagicMock()
    source.source_name = name
    source.needs_enrichment = False
    source.search = AsyncMock(return_value=(jobs, 2))
    return source


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_jobs_in_db(test_app, monkeypatch):
    """Synced jobs are persisted and retrievable from the DB."""
    jobs = [_make_job_data(i) for i in range(3)]
    mock_source = _mock_source("adzuna", jobs)
    empty_jsearch = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_jsearch)
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
            select(Job).where(Job.source == "adzuna", Job.is_active.is_(True))
        )
        db_jobs = list(result.scalars().all())

    assert len(db_jobs) == 3
    titles = {j.title for j in db_jobs}
    assert titles == {"Python Engineer #0", "Python Engineer #1", "Python Engineer #2"}


@pytest.mark.asyncio
async def test_resync_is_idempotent(test_app, monkeypatch):
    """Re-syncing the same jobs returns new_jobs=0, updated_jobs=3, and the DB still has 3 rows."""
    jobs = [_make_job_data(i) for i in range(3)]
    mock_source = _mock_source("adzuna", jobs)
    empty_jsearch = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_jsearch)
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
        result = await session.execute(select(Job).where(Job.source == "adzuna"))
        db_jobs = list(result.scalars().all())

    assert len(db_jobs) == 3


@pytest.mark.asyncio
async def test_posted_at_timezone_roundtrip(test_app, monkeypatch):
    """
    Regression: timezone-aware posted_at must survive INSERT and SELECT without error.

    JSearch returns datetimes with tzinfo=UTC. Before the timestamptz migration,
    this caused: asyncpg.exceptions.DataError: can't subtract offset-naive and
    offset-aware datetimes on commit.
    """
    aware_posted_at = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    jobs = [_make_job_data(0, posted_at=aware_posted_at)]
    mock_source = _mock_source("jsearch", jobs)

    empty_adzuna = _mock_source("adzuna", [])
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: empty_adzuna)
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200
    assert resp.json()["new_jobs"] == 1

    # Read back and assert timezone info is preserved
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Job).where(Job.source == "jsearch"))
        job = result.scalar_one()

    assert job.posted_at is not None
    assert job.posted_at.tzinfo is not None
    # Value should round-trip correctly (Postgres timestamptz normalises to UTC)
    assert job.posted_at.replace(tzinfo=UTC) == aware_posted_at


@pytest.mark.asyncio
async def test_jsearch_source_stored_with_correct_source_name(test_app, monkeypatch):
    """Jobs from JSearch are stored with source='jsearch'."""
    jobs = [_make_job_data(0, source_name="jsearch")]
    mock_source = _mock_source("jsearch", jobs)
    empty_adzuna = _mock_source("adzuna", [])

    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: empty_adzuna)
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200
    assert resp.json()["new_jobs"] == 1

    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Job).where(Job.source == "jsearch"))
        job = result.scalar_one()

    assert job.source == "jsearch"
    assert job.external_id == "sync-e2e-jsearch-000"


@pytest.mark.asyncio
async def test_stale_job_marking(test_app, monkeypatch):
    """
    Jobs not refreshed within stale_after_days are marked is_active=False on next sync.
    """
    jobs = [_make_job_data(0), _make_job_data(1)]
    mock_source = _mock_source("adzuna", jobs)
    empty_jsearch = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_jsearch)
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    # Initial sync
    resp = await test_app.post("/api/jobs/sync")
    assert resp.json()["new_jobs"] == 2

    # Backdate one job's fetched_at to 20 days ago (beyond default 14-day stale threshold)
    from app.database import get_session_factory
    from app.models.job import Job

    factory = get_session_factory()
    stale_job_id: uuid.UUID
    async with factory() as session:
        result = await session.execute(
            select(Job).where(Job.source == "adzuna", Job.external_id == "sync-e2e-adzuna-000")
        )
        stale_job = result.scalar_one()
        stale_job.fetched_at = datetime.now(UTC) - timedelta(days=20)
        session.add(stale_job)
        await session.commit()
        stale_job_id = stale_job.id

    # Second sync: marks the backdated job stale
    resp2 = await test_app.post("/api/jobs/sync")
    assert resp2.status_code == 200
    assert resp2.json()["stale_jobs"] >= 1

    async with factory() as session:
        result = await session.execute(select(Job).where(Job.id == stale_job_id))
        refreshed = result.scalar_one()

    assert refreshed.is_active is False
