import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
import sqlalchemy as sa

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.slug_fetch import SlugFetch
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.scheduler.tasks import _enqueue_batch_match_for_affected_profiles
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE
from app.worker.handlers.fetch_slug import FetchSlugHandler


@pytest.fixture(autouse=True)
def reset_settings_cache():
    import app.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None


async def _seed_job(db_session) -> Job:
    job = Job(
        source="greenhouse",
        external_id=f"batch-match-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Acme",
        apply_url="https://example.com/jobs/backend",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


async def _seed_profile(db_session) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"batch-match-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(user_id=user.id)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _seed_interested_profile(db_session, slug: str) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"fetch-batch-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    company = Company(
        canonical_name=slug.title(),
        normalized_key=f"{slug}-{uuid.uuid4()}",
        provider_slugs={"greenhouse": slug},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[company.id],
        search_active=True,
    )
    db_session.add(profile)
    db_session.add(SlugFetch(source="greenhouse", slug=slug))
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _seed_application(
    db_session,
    *,
    job_id,
    profile_id,
    status: str = "pending_review",
    match_score: float | None = None,
) -> Application:
    app = Application(
        job_id=job_id,
        profile_id=profile_id,
        status=status,
        match_score=match_score,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


def _greenhouse_fixture(slug: str, external_id: int = 9001) -> dict:
    return {
        "jobs": [
            {
                "id": external_id,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{external_id}",
                "updated_at": datetime.now(UTC).isoformat(),
                "content": "<p>job</p>",
            }
        ]
    }


def _fetch_row(slug: str) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="fetch-slug",
        payload={"provider": "greenhouse", "slug": slug},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="w1",
    )


@pytest.mark.asyncio
async def test_enqueue_batch_match_for_affected_profiles_when_enabled(db_session, monkeypatch):
    import app.config as cfg

    monkeypatch.setenv("BATCH_MATCH_ENABLED", "true")
    cfg._settings = None

    job = await _seed_job(db_session)
    other_job = await _seed_job(db_session)
    first_profile = await _seed_profile(db_session)
    second_profile = await _seed_profile(db_session)
    scored_profile = await _seed_profile(db_session)
    dismissed_profile = await _seed_profile(db_session)
    other_job_profile = await _seed_profile(db_session)

    await _seed_application(db_session, job_id=job.id, profile_id=first_profile.id)
    await _seed_application(
        db_session,
        job_id=job.id,
        profile_id=second_profile.id,
        status="auto_rejected",
    )
    await _seed_application(
        db_session,
        job_id=job.id,
        profile_id=scored_profile.id,
        match_score=0.8,
    )
    await _seed_application(
        db_session,
        job_id=job.id,
        profile_id=dismissed_profile.id,
        status="dismissed",
    )
    await _seed_application(db_session, job_id=other_job.id, profile_id=other_job_profile.id)

    enqueued = await _enqueue_batch_match_for_affected_profiles(job.id, db_session)
    await db_session.commit()

    rows = (
        await db_session.execute(
            sa.select(WorkQueue).where(WorkQueue.job_type == "batch-match")
        )
    ).scalars().all()

    assert enqueued == 2
    assert {(row.payload["profile_id"], row.dedupe_key) for row in rows} == {
        (str(first_profile.id), f"batch-match:{first_profile.id}"),
        (str(second_profile.id), f"batch-match:{second_profile.id}"),
    }


@pytest.mark.asyncio
async def test_enqueue_batch_match_returns_zero_when_disabled(db_session, monkeypatch):
    import app.config as cfg

    monkeypatch.setenv("BATCH_MATCH_ENABLED", "false")
    cfg._settings = None

    job = await _seed_job(db_session)
    profile = await _seed_profile(db_session)
    await _seed_application(db_session, job_id=job.id, profile_id=profile.id)

    enqueued = await _enqueue_batch_match_for_affected_profiles(job.id, db_session)
    await db_session.commit()

    batch_match_count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(WorkQueue)
            .where(WorkQueue.job_type == "batch-match")
        )
    ).scalar_one()

    assert enqueued == 0
    assert batch_match_count == 0


@pytest.mark.asyncio
async def test_fetch_slug_enqueues_batch_match_instead_of_match_when_enabled(
    db_session,
    monkeypatch,
):
    import app.config as cfg

    monkeypatch.setenv("BATCH_MATCH_ENABLED", "true")
    cfg._settings = None

    slug = "figma"
    profile = await _seed_interested_profile(db_session, slug)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/{slug}/jobs").mock(
            return_value=httpx.Response(200, json=_greenhouse_fixture(slug))
        )
        await FetchSlugHandler()(db_session, _fetch_row(slug))

    batch_match_rows = (
        await db_session.execute(
            sa.select(WorkQueue).where(WorkQueue.job_type == "batch-match")
        )
    ).scalars().all()
    match_count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(WorkQueue)
            .where(WorkQueue.job_type == "match")
        )
    ).scalar_one()

    assert match_count == 0
    assert [(row.payload, row.dedupe_key) for row in batch_match_rows] == [
        ({"profile_id": str(profile.id)}, f"batch-match:{profile.id}")
    ]
