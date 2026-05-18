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
        description_raw="We need a Python engineer.",
        posted_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_upsert_job_creates_new(db_session):
    job_data = make_job_data()
    job, created = await upsert_job(job_data, "greenhouse", db_session)

    assert created is True
    assert job.id is not None
    assert job.title == "Python Engineer"
    assert job.source == "greenhouse"
    assert job.external_id == "job-001"


@pytest.mark.asyncio
async def test_upsert_job_updates_existing(db_session):
    job_data = make_job_data()
    job1, created1 = await upsert_job(job_data, "greenhouse", db_session)
    assert created1 is True

    updated_data = make_job_data(title="Senior Python Engineer")
    job2, created2 = await upsert_job(updated_data, "greenhouse", db_session)
    assert created2 is False
    assert job2.id == job1.id
    assert job2.title == "Senior Python Engineer"


@pytest.mark.asyncio
async def test_mark_stale_jobs(db_session):
    from datetime import timedelta

    job_data = make_job_data()
    job, _ = await upsert_job(job_data, "greenhouse", db_session)

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
    """sync_profile enqueues work and never performs synchronous scoring."""
    from app.models.company import Company
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    company_ids: list[uuid.UUID] = []
    for slug in ("airbnb", "stripe"):
        company = Company(
            canonical_name=slug.title(),
            normalized_key=f"{slug}-{uuid.uuid4()}",
            provider_slugs={"greenhouse": slug},
            resolved_at=datetime.now(UTC),
        )
        db_session.add(company)
        await db_session.commit()
        await db_session.refresh(company)
        company_ids.append(company.id)
    profile.target_company_slugs = {"greenhouse": ["airbnb", "stripe"]}
    profile.target_company_ids = company_ids
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)

    assert result["status"] == "queued"
    assert sorted(result["queued_slugs"]) == ["airbnb", "stripe"]
    assert result["matched_now"] == 0


@pytest.mark.asyncio
async def test_sync_active_profiles_uses_shared_enqueue_contract(db_session):
    """Cron and scheduler sweeps use the same enqueue-only behavior as manual sync."""
    from app.models.company import Company
    from app.models.slug_fetch import SlugFetch
    from app.models.user import User
    from app.models.user_profile import UserProfile
    from app.models.work_queue import WorkQueue

    active_company = Company(
        canonical_name="Active Co",
        normalized_key=f"active-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "active-co"},
        resolved_at=datetime.now(UTC),
    )
    inactive_company = Company(
        canonical_name="Inactive Co",
        normalized_key=f"inactive-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "inactive-co"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([active_company, inactive_company])
    await db_session.commit()
    await db_session.refresh(active_company)
    await db_session.refresh(inactive_company)

    active_user = User(id=uuid.uuid4(), email=f"active-{uuid.uuid4()}@test.com")
    inactive_user = User(id=uuid.uuid4(), email=f"inactive-{uuid.uuid4()}@test.com")
    db_session.add_all([active_user, inactive_user])
    await db_session.commit()

    db_session.add_all(
        [
            UserProfile(
                user_id=active_user.id,
                email=active_user.email,
                search_active=True,
                target_company_ids=[active_company.id],
            ),
            UserProfile(
                user_id=inactive_user.id,
                email=inactive_user.email,
                search_active=False,
                target_company_ids=[inactive_company.id],
            ),
            SlugFetch(
                source="greenhouse",
                slug="active-co",
                last_fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            SlugFetch(
                source="greenhouse",
                slug="inactive-co",
                last_fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.commit()

    result = await job_sync_service.sync_active_profiles(db_session)

    assert result == {
        "enqueued": ["active-co"],
        "pruned": 0,
        "active_profiles": 1,
        "profiles_enqueued": 1,
    }
    queue_rows = (
        (
            await db_session.execute(
                select(WorkQueue).where(WorkQueue.job_type == "fetch-slug")
            )
        )
        .scalars()
        .all()
    )
    assert [row.dedupe_key for row in queue_rows] == ["fetch-slug:greenhouse:active-co"]


@pytest.mark.asyncio
async def test_sync_profile_prunes_invalid_provider_slugs_from_company(db_session):
    """sync_profile drops (provider, slug) entries from Company.provider_slugs
    when their SlugFetch row is marked is_invalid=True. The pruned-slugs summary
    is now a list of "provider:slug" strings (was bare slug strings under the
    legacy target_company_slugs path)."""
    from app.models.company import Company
    from app.models.slug_fetch import SlugFetch
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)

    company_ids: list[uuid.UUID] = []
    for slug in ("airbnb", "deadcorp", "stripe"):
        company = Company(
            canonical_name=slug.title(),
            normalized_key=f"{slug}-{uuid.uuid4()}",
            provider_slugs={"greenhouse": slug},
            resolved_at=datetime.now(UTC),
        )
        db_session.add(company)
        await db_session.commit()
        await db_session.refresh(company)
        company_ids.append(company.id)
    deadcorp_id = company_ids[1]
    profile.target_company_ids = company_ids
    db_session.add(profile)
    db_session.add(SlugFetch(source="greenhouse", slug="deadcorp", is_invalid=True))
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)

    assert result["pruned_slugs"] == ["greenhouse:deadcorp"]
    assert "deadcorp" not in result["queued_slugs"]
    deadcorp = await db_session.get(Company, deadcorp_id)
    await db_session.refresh(deadcorp)
    assert deadcorp.provider_slugs == {}
    assert deadcorp.unfollowable is True


@pytest.mark.asyncio
async def test_sync_profile_no_longer_seeds_defaults(db_session):
    """seed_defaults_if_empty is gone (default-seeding moved to the onboarding
    agent + company_resolver path). sync_profile must not write anything to
    the deprecated target_company_slugs JSONB column."""
    from app.models.company import Company
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    # Pre-seed two Company rows + target_company_ids so work_queue enqueueing has
    # something to do, while leaving target_company_slugs empty to prove the
    # legacy seeding path is gone.
    company_ids: list[uuid.UUID] = []
    for slug in ("airbnb", "stripe"):
        company = Company(
            canonical_name=slug.title(),
            normalized_key=f"{slug}-{uuid.uuid4()}",
            provider_slugs={"greenhouse": slug},
            resolved_at=datetime.now(UTC),
        )
        db_session.add(company)
        await db_session.commit()
        await db_session.refresh(company)
        company_ids.append(company.id)
    profile.target_company_slugs = {}
    profile.target_company_ids = company_ids
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)
    assert sorted(result["queued_slugs"]) == ["airbnb", "stripe"]
    await db_session.refresh(profile)
    # legacy column untouched
    assert profile.target_company_slugs == {}


@pytest.mark.asyncio
async def test_upsert_job_populates_description(db_session):
    """upsert_job should compute description (markdown) from raw HTML description_raw."""
    raw = "<h2>About</h2><ul><li><strong>Python</strong></li></ul>"
    data = JobData(
        external_id="ext-clean-1",
        title="Test Engineer",
        company_name="Test Co",
        location="Remote",
        workplace_type="remote",
        description_raw=raw,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/1",
        posted_at=None,
    )
    job, created = await upsert_job(data, "greenhouse", db_session)
    assert created is True
    assert job.description is not None
    assert "## About" in job.description
    assert "**Python**" in job.description
    assert "<h2>" not in job.description


@pytest.mark.asyncio
async def test_upsert_job_preserves_description_beyond_prompt_cap(db_session):
    from app.agents.matching_agent import MAX_JOB_DESC_CHARS, truncate_description

    raw_body = "remote policy detail " * 1200
    raw = f"<h2>Role</h2><p>{raw_body}</p>"
    assert len(raw) > MAX_JOB_DESC_CHARS

    data = JobData(
        external_id="ext-long-description-1",
        title="Test Engineer",
        company_name="Test Co",
        location="Remote",
        workplace_type="remote",
        description_raw=raw,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/long-description-1",
        posted_at=None,
    )

    job, _ = await upsert_job(data, "greenhouse", db_session)

    assert job.description_raw == raw
    assert job.description is not None
    assert len(job.description) > MAX_JOB_DESC_CHARS
    prompt_fragment = truncate_description(job.description)
    assert len(prompt_fragment) < len(job.description)
    assert prompt_fragment.endswith("[Description truncated]")


@pytest.mark.asyncio
async def test_upsert_job_recomputes_description_on_update(db_session):
    """Re-upserting an existing job recomputes description."""
    data = JobData(
        external_id="ext-clean-2",
        title="Test Engineer",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_raw="<p>v1</p>",
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/2",
        posted_at=None,
    )
    await upsert_job(data, "greenhouse", db_session)

    data.description_raw = "<p>v2 updated</p>"
    job, created = await upsert_job(data, "greenhouse", db_session)
    assert created is False
    assert "v2 updated" in (job.description or "")
    assert "v1" not in (job.description or "")


@pytest.mark.asyncio
async def test_upsert_job_handles_none_description(db_session):
    """A job with no description should store description='' (or None — both safe)."""
    data = JobData(
        external_id="ext-clean-3",
        title="Title only",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_raw=None,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/3",
        posted_at=None,
    )
    job, _ = await upsert_job(data, "greenhouse", db_session)
    assert job.description in ("", None)
