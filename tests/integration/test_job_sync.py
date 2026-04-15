"""
Integration tests for job sync pipeline.
Tests: Adzuna mock → upsert → DB assertions, staleness, cursor updates.
"""

import uuid
from datetime import datetime
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
        search_keywords=["python engineer"],
        remote_ok=True,
    )


def make_job_data(external_id: str = "job-001", title: str = "Python Engineer") -> JobData:
    return JobData(
        external_id=external_id,
        title=title,
        company_name="Acme Corp",
        location="New York",
        apply_url="https://boards.greenhouse.io/acme/jobs/12345",
        ats_type="greenhouse",
        supports_api_apply=True,
        description_md="We need a Python engineer.",
        posted_at=datetime.utcnow(),
    )


@pytest.mark.asyncio
async def test_upsert_job_creates_new(db_session):
    job_data = make_job_data()
    job, created = await upsert_job(job_data, "adzuna", db_session)

    assert created is True
    assert job.id is not None
    assert job.title == "Python Engineer"
    assert job.source == "adzuna"
    assert job.external_id == "job-001"
    assert job.supports_api_apply is True


@pytest.mark.asyncio
async def test_upsert_job_updates_existing(db_session):
    job_data = make_job_data()
    job1, created1 = await upsert_job(job_data, "adzuna", db_session)
    assert created1 is True

    updated_data = make_job_data(title="Senior Python Engineer")
    job2, created2 = await upsert_job(updated_data, "adzuna", db_session)
    assert created2 is False
    assert job2.id == job1.id
    assert job2.title == "Senior Python Engineer"


@pytest.mark.asyncio
async def test_upsert_job_idempotent_different_sources(db_session):
    """Same external_id from two different sources creates two separate rows."""
    job_data = make_job_data()
    job_adzuna, _ = await upsert_job(job_data, "adzuna", db_session)
    job_other, _ = await upsert_job(job_data, "linkedin", db_session)

    assert job_adzuna.id != job_other.id


@pytest.mark.asyncio
async def test_mark_stale_jobs(db_session):
    from datetime import timedelta

    job_data = make_job_data()
    job, _ = await upsert_job(job_data, "adzuna", db_session)

    # Manually backdate fetched_at to simulate staleness
    job.fetched_at = datetime.utcnow() - timedelta(days=20)
    db_session.add(job)
    await db_session.commit()

    stale_count = await mark_stale_jobs(stale_after_days=14, session=db_session)
    assert stale_count == 1

    result = await db_session.execute(select(Job).where(Job.id == job.id))
    refreshed = result.scalar_one()
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_sync_profile_with_mocked_source(db_session):
    """Full sync_profile run using a mock JobSource."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="test@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = await get_or_create_profile(user.id, db_session)
    profile.search_keywords = ["python engineer"]
    profile.target_locations = ["New York"]
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    # Create a mock source that returns one job
    mock_source = MagicMock()
    mock_source.source_name = "mock_source"
    mock_source.search = AsyncMock(return_value=([make_job_data()], 2))

    result = await job_sync_service.sync_profile(profile, db_session, sources=[mock_source])

    assert result["new_jobs"] == 1
    assert result["updated_jobs"] == 0

    # Verify job was upserted
    jobs_result = await db_session.execute(
        select(Job).where(Job.source == "mock_source")
    )
    jobs = jobs_result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].title == "Python Engineer"

    # Verify cursor was updated in profile
    await db_session.refresh(profile)
    assert "mock_source" in profile.source_cursors
