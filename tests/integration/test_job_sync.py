"""Integration tests for the (Greenhouse-only) job sync pipeline."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.models.job import Job
from app.services import job_sync_service
from app.services.job_service import mark_stale_jobs, upsert_job
from app.sources.base import JobData


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
async def test_sync_profile_returns_202_shape_and_enqueues_stale_slugs(db_session):
    """The new contract: sync_profile is enqueue-only + score-cached, returns
    {status:'queued', queued_slugs:[...], matched_now:int}, never blocks on fetch."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {"greenhouse": ["airbnb", "stripe"]}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)

    assert result["status"] == "queued"
    assert sorted(result["queued_slugs"]) == ["airbnb", "stripe"]
    assert result["matched_now"] == 0  # no cached jobs yet


@pytest.mark.asyncio
async def test_sync_profile_prunes_invalid_slugs_from_profile(db_session):
    """sync_profile removes slugs from profile.target_company_slugs.greenhouse
    when their SlugFetch row is marked is_invalid=True. The banner that says
    "we removed [slugs]" was lying — this test pins the new behaviour."""
    from app.models.slug_fetch import SlugFetch
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {"greenhouse": ["airbnb", "deadcorp", "stripe"]}
    db_session.add(profile)
    db_session.add(SlugFetch(source="greenhouse_board", slug="deadcorp", is_invalid=True))
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)

    assert result["pruned_slugs"] == ["deadcorp"]
    assert "deadcorp" not in result["queued_slugs"]
    await db_session.refresh(profile)
    assert profile.target_company_slugs["greenhouse"] == ["airbnb", "stripe"]


@pytest.mark.asyncio
async def test_sync_profile_seeds_defaults_when_empty(db_session):
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    # explicitly empty
    profile.target_company_slugs = {}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)
    assert result["seeded_defaults"] is True
    assert len(result["queued_slugs"]) == 5
    await db_session.refresh(profile)
    assert len(profile.target_company_slugs["greenhouse"]) == 5


@pytest.mark.asyncio
async def test_upsert_job_populates_description_clean(db_session):
    """upsert_job should compute description_clean from raw HTML description_md."""
    raw = "<h2>About</h2><ul><li><strong>Python</strong></li></ul>"
    data = JobData(
        external_id="ext-clean-1",
        title="Test Engineer",
        company_name="Test Co",
        location="Remote",
        workplace_type="remote",
        description_md=raw,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/1",
        posted_at=None,
    )
    job, created = await upsert_job(data, "greenhouse_board", db_session)
    assert created is True
    assert job.description_clean is not None
    assert "## About" in job.description_clean
    assert "**Python**" in job.description_clean
    assert "<h2>" not in job.description_clean


@pytest.mark.asyncio
async def test_upsert_job_recomputes_description_clean_on_update(db_session):
    """Re-upserting an existing job recomputes description_clean."""
    data = JobData(
        external_id="ext-clean-2",
        title="Test Engineer",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_md="<p>v1</p>",
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/2",
        posted_at=None,
    )
    await upsert_job(data, "greenhouse_board", db_session)

    data.description_md = "<p>v2 updated</p>"
    job, created = await upsert_job(data, "greenhouse_board", db_session)
    assert created is False
    assert "v2 updated" in (job.description_clean or "")
    assert "v1" not in (job.description_clean or "")


@pytest.mark.asyncio
async def test_upsert_job_handles_none_description(db_session):
    """A job with no description should store description_clean='' (or None — both safe)."""
    data = JobData(
        external_id="ext-clean-3",
        title="Title only",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_md=None,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/3",
        posted_at=None,
    )
    job, _ = await upsert_job(data, "greenhouse_board", db_session)
    assert job.description_clean in ("", None)
