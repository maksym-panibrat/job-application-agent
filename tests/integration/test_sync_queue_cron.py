"""Integration test for run_sync_queue cron worker."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_sync_queue
from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


async def _seed_profile(db_session, *slugs: str) -> UserProfile:
    """Seed a User + UserProfile (FK constraint requires the user row first).

    Sets target_company_ids (read by enqueue_stale,
    _prune_invalid_provider_slugs, and the matching pipeline post-D6). The
    legacy target_company_slugs JSONB is left at its default ({}) — every
    read path now goes through Company.
    """
    user = User(id=uuid.uuid4(), email=f"sync-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    company_ids: list[uuid.UUID] = []
    for slug in slugs:
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
    profile = UserProfile(
        user_id=user.id,
        target_company_ids=company_ids,
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_run_sync_queue_fetches_claimed_slugs_and_enqueues_matches(db_session):
    profile = await _seed_profile(db_session, "airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    fixture = {
        "jobs": [
            {
                "id": 9001,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/9001",
                "updated_at": datetime.now(UTC).isoformat(),
                "content": "<p>job</p>",
            }
        ]
    }
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb/jobs").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        result = await run_sync_queue()

    assert result["fetched"] == 1
    jobs = (await db_session.execute(sa.select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].company_name == slug_to_company_name("airbnb")
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 1
    assert apps[0].match_status == "pending_match"


@pytest.mark.asyncio
async def test_run_job_sync_bulk_enqueues_for_active_profiles(db_session):
    """The 6h /internal/cron/sync becomes a bulk-enqueue: it does not fetch directly
    but seeds the slug_fetches queue for every active profile's stale slugs."""
    from app.scheduler.tasks import run_job_sync

    await _seed_profile(db_session, "airbnb", "stripe")
    p_inactive = await _seed_profile(db_session, "notion")
    p_inactive.search_active = False
    db_session.add(p_inactive)
    await db_session.commit()

    summary = await run_job_sync()
    assert summary["profiles_enqueued"] == 1
    assert summary["slugs_enqueued"] == 2
    # `run_job_sync` commits via a separate session; drop in-memory cache so the
    # pending_count reflects the worker-committed state.
    db_session.expire_all()
    # airbnb + stripe queued (notion skipped because profile is inactive)
    pending = await slug_registry_service.pending_count(db_session)
    assert pending == 2


@pytest.mark.asyncio
async def test_run_job_sync_does_not_synchronously_score_cached_jobs(db_session):
    """The 6h /internal/cron/sync must NOT call score_cached. Bulk cron has no UI
    to give "instant feedback" to and runs against N profiles inside Cloud Run's
    300s wall — synchronous LLM scoring blew that budget (HTTP 504, issue #70).
    score_and_match belongs in run_match_queue (every 5min, deadline-bounded)."""
    from app.scheduler.tasks import run_job_sync

    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id
    job = Job(
        source="greenhouse",
        external_id="9001",
        title="Backend Engineer",
        company_name=slug_to_company_name("airbnb"),
        apply_url="https://boards.greenhouse.io/airbnb/jobs/9001",
        description="job",
    )
    db_session.add(job)
    await db_session.commit()

    summary = await run_job_sync()

    # Cron pass should enqueue the slug but not score the cached job.
    assert summary["slugs_enqueued"] == 1
    # `run_job_sync` commits via a separate session; drop in-memory cache so we
    # re-read the worker-committed state.
    db_session.expire_all()
    scores = (
        (
            await db_session.execute(
                sa.select(Application.match_score).where(Application.profile_id == profile_id)
            )
        )
        .scalars()
        .all()
    )
    # Either zero Application rows (score_cached never ran, so never created one),
    # or rows exist with match_score=None. Both prove no LLM scoring happened.
    assert all(s is None for s in scores), (
        f"run_job_sync invoked synchronous LLM scoring (match_scores={scores!r}); "
        "this is the regression that caused the recurring 504 in /internal/cron/sync."
    )


@pytest.mark.asyncio
async def test_run_job_sync_prunes_invalid_slugs_for_active_profiles(db_session):
    """The 6h /internal/cron/sync now also prunes is_invalid=True (provider, slug)
    pairs from the Company rows the profile follows (closes the gap where prune
    only ran on user-initiated sync, never on the cron sweep). Companies whose
    provider_slugs become empty are flagged unfollowable=True."""
    from app.models.slug_fetch import SlugFetch
    from app.scheduler.tasks import run_job_sync

    profile = await _seed_profile(db_session, "airbnb", "deadcorp", "stripe")
    db_session.add(SlugFetch(source="greenhouse", slug="deadcorp", is_invalid=True))
    await db_session.commit()

    summary = await run_job_sync()

    assert summary["slugs_pruned"] == 1
    db_session.expire_all()
    await db_session.refresh(profile)
    # The deadcorp Company row should have empty provider_slugs and be unfollowable.
    stmt = sa.select(Company).where(Company.id.in_(profile.target_company_ids))
    rows = (await db_session.execute(stmt)).scalars().all()
    by_slug = {r.canonical_name.lower(): r for r in rows}
    assert by_slug["deadcorp"].provider_slugs == {}
    assert by_slug["deadcorp"].unfollowable is True
    assert by_slug["airbnb"].provider_slugs == {"greenhouse": "airbnb"}
    assert by_slug["airbnb"].unfollowable is False
    assert by_slug["stripe"].provider_slugs == {"greenhouse": "stripe"}


@pytest.mark.asyncio
async def test_run_sync_queue_marks_invalid_after_2_404s(db_session):
    profile = await _seed_profile(db_session, "openai")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(return_value=httpx.Response(404))
        await run_sync_queue()
    row = await slug_registry_service.get("greenhouse", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False

    # Re-queue + run again
    row.queued_at = datetime.now(UTC)
    await db_session.commit()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(return_value=httpx.Response(404))
        await run_sync_queue()
    # Drop in-memory cache so we re-read the row state the worker (in a separate
    # session) committed.
    db_session.expire_all()
    row = await slug_registry_service.get("greenhouse", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True
