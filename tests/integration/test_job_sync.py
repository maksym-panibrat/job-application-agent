"""Integration tests for the (Greenhouse-only) job sync pipeline."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import select

from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services import job_sync_service
from app.services.job_service import mark_stale_jobs, upsert_job
from app.sources.base import JobData


def make_profile() -> UserProfile:
    user_id = uuid.uuid4()
    return UserProfile(
        user_id=user_id,
        full_name="Test User",
        target_roles=["Backend Engineer"],
        target_locations=["New York"],
        target_company_slugs={"greenhouse": ["acme"]},
        remote_ok=True,
    )


def make_job_data(external_id: str = "job-001", title: str = "Python Engineer") -> JobData:
    return JobData(
        external_id=external_id,
        title=title,
        company_name="Acme Corp",
        location="New York",
        apply_url="https://boards.greenhouse.io/acme/jobs/12345",
        description_md="We need a Python engineer.",
        posted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_upsert_job_creates_new(db_session):
    job_data = make_job_data()
    job, created = await upsert_job(job_data, "greenhouse_board", db_session)

    assert created is True
    assert job.id is not None
    assert job.title == "Python Engineer"
    assert job.source == "greenhouse_board"
    assert job.external_id == "job-001"


@pytest.mark.asyncio
async def test_upsert_job_updates_existing(db_session):
    job_data = make_job_data()
    job1, created1 = await upsert_job(job_data, "greenhouse_board", db_session)
    assert created1 is True

    updated_data = make_job_data(title="Senior Python Engineer")
    job2, created2 = await upsert_job(updated_data, "greenhouse_board", db_session)
    assert created2 is False
    assert job2.id == job1.id
    assert job2.title == "Senior Python Engineer"


@pytest.mark.asyncio
async def test_mark_stale_jobs(db_session):
    from datetime import timedelta

    job_data = make_job_data()
    job, _ = await upsert_job(job_data, "greenhouse_board", db_session)

    job.fetched_at = datetime.now(UTC) - timedelta(days=20)
    db_session.add(job)
    await db_session.commit()

    stale_count = await mark_stale_jobs(stale_after_days=14, session=db_session)
    assert stale_count == 1

    result = await db_session.execute(select(Job).where(Job.id == job.id))
    refreshed = result.scalar_one()
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_sync_profile_with_mocked_source(db_session):
    """sync_profile fetches per slug, dedups, upserts."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="test@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {"greenhouse": ["acme"]}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    mock_source = MagicMock()
    mock_source.source_name = "greenhouse_board"
    mock_source.search = AsyncMock(return_value=([make_job_data()], None))

    result = await job_sync_service.sync_profile(profile, db_session, sources=[mock_source])

    assert result["new_jobs"] == 1
    assert result["updated_jobs"] == 0

    jobs_result = await db_session.execute(
        select(Job).where(Job.source == "greenhouse_board")
    )
    jobs = jobs_result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].title == "Python Engineer"


@pytest.mark.asyncio
async def test_sync_profile_no_slugs_short_circuits(db_session):
    """Profile without target_company_slugs.greenhouse short-circuits without fetching."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="noslugs@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    mock_source = MagicMock()
    mock_source.source_name = "greenhouse_board"
    mock_source.search = AsyncMock(return_value=([], None))

    result = await job_sync_service.sync_profile(profile, db_session, sources=[mock_source])

    assert result["new_jobs"] == 0
    mock_source.search.assert_not_called()


@pytest.mark.asyncio
async def test_sync_profile_dedups_within_slug(db_session):
    """Same (title, company) returned twice from one slug is upserted once."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="dedup@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {"greenhouse": ["acme"]}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    duplicate = [
        make_job_data(external_id="job-001"),
        make_job_data(external_id="job-002"),  # different external_id, same (title, company)
    ]
    mock_source = MagicMock()
    mock_source.source_name = "greenhouse_board"
    mock_source.search = AsyncMock(return_value=(duplicate, None))

    result = await job_sync_service.sync_profile(profile, db_session, sources=[mock_source])

    assert result["new_jobs"] == 1
