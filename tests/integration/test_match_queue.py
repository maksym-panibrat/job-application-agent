"""Integration tests for the per-(profile, job) match queue."""

import uuid

import pytest
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import match_queue_service


def _job(slug: str, ext: str = "1") -> Job:
    return Job(
        source="greenhouse",
        external_id=f"{slug}-{ext}",
        title="Engineer",
        company_name=slug_to_company_name(slug),
        apply_url=f"https://x/{slug}/{ext}",
        is_active=True,
    )


async def _seed_profile(db_session, *slugs: str) -> UserProfile:
    """Seed a User + UserProfile (FK constraint requires the user row first)."""
    user = User(id=uuid.uuid4(), email=f"mq-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": list(slugs)},
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_enqueue_creates_application_for_each_interested_profile(db_session):
    job = _job("airbnb")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    p_a = await _seed_profile(db_session, "airbnb", "stripe")
    p_b = await _seed_profile(db_session, "airbnb")
    await _seed_profile(db_session, "notion")  # not interested

    enqueued = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert enqueued == 2

    apps = (
        (await db_session.execute(sa.select(Application).where(Application.job_id == job.id)))
        .scalars()
        .all()
    )
    profile_ids = {a.profile_id for a in apps}
    assert profile_ids == {p_a.id, p_b.id}
    for a in apps:
        assert a.match_status == "pending_match"
        assert a.match_queued_at is not None


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_on_conflict(db_session):
    job = _job("airbnb")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await _seed_profile(db_session, "airbnb")

    first = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    second = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert first == 1
    assert second == 0  # ON CONFLICT DO NOTHING


@pytest.mark.asyncio
async def test_enqueue_skips_inactive_profiles(db_session):
    job = _job("airbnb")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    inactive = await _seed_profile(db_session, "airbnb")
    inactive.search_active = False
    db_session.add(inactive)
    await db_session.commit()

    enqueued = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert enqueued == 0


@pytest.mark.asyncio
async def test_next_batch_claims_oldest_first(db_session):
    await _seed_profile(db_session, "airbnb")
    db_session.add_all([_job("airbnb", str(i)) for i in range(3)])
    await db_session.commit()
    for j in (await db_session.execute(sa.select(Job))).scalars():
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    batch = await match_queue_service.next_batch(db_session, limit=2)
    assert len(batch) == 2
    assert all(a.match_claimed_at is not None for a in batch)


@pytest.mark.asyncio
async def test_mark_done_clears_claim(db_session):
    await _seed_profile(db_session, "airbnb")
    db_session.add(_job("airbnb"))
    await db_session.commit()
    job = (await db_session.execute(sa.select(Job))).scalar_one()
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    [app] = await match_queue_service.next_batch(db_session, limit=10)

    await match_queue_service.mark_done(app.id, db_session)
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one()
    assert refreshed.match_status == "matched"
    assert refreshed.match_queued_at is None
    assert refreshed.match_claimed_at is None


@pytest.mark.asyncio
async def test_mark_error_after_3_attempts(db_session):
    await _seed_profile(db_session, "airbnb")
    db_session.add(_job("airbnb"))
    await db_session.commit()
    job = (await db_session.execute(sa.select(Job))).scalar_one()
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    [app] = await match_queue_service.next_batch(db_session, limit=10)

    for _ in range(3):
        await match_queue_service.mark_attempt_failed(app.id, db_session)
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one()
    assert refreshed.match_status == "error"
    assert refreshed.match_attempts == 3


@pytest.mark.asyncio
async def test_audit_and_recover_error_apps(db_session):
    """audit_error_apps + recover_error_apps round-trip:
    seed mixed apps (some error, some pending_match, some matched), audit,
    then recover and assert the error apps flip back to pending_match while
    others are untouched. Mirrors the 2026-05-04 prod recovery scenario (#75)."""
    from datetime import UTC, datetime, timedelta

    p1 = await _seed_profile(db_session, "airbnb")
    p2 = await _seed_profile(db_session, "stripe")

    # Seed jobs for each profile + create their pending applications
    for slug, profile in (("airbnb", p1), ("stripe", p2)):
        for ext in ("a", "b"):
            db_session.add(_job(slug, ext))
    await db_session.commit()
    jobs = (await db_session.execute(sa.select(Job))).scalars().all()
    for job in jobs:
        await match_queue_service.enqueue_for_interested_profiles(job, db_session)

    # Get all the just-created applications
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 4  # 2 jobs per profile × 2 profiles, each scoped to one profile

    # Force two of them into match_status='error' (simulating depletion outcome)
    for app in apps[:2]:
        for _ in range(3):
            await match_queue_service.mark_attempt_failed(app.id, db_session)

    db_session.expire_all()

    # Audit — all profiles, no time bound
    rows = await match_queue_service.audit_error_apps(db_session)
    total = sum(r["count"] for r in rows)
    assert total == 2, f"expected 2 error apps, got {total} ({rows})"

    # Audit with `since` in the future → should match nothing
    future = datetime.now(UTC) + timedelta(days=1)
    rows_future = await match_queue_service.audit_error_apps(db_session, since=future)
    assert rows_future == []

    # Recover — re-queue all error apps
    recovered = await match_queue_service.recover_error_apps(db_session)
    assert recovered == 2

    db_session.expire_all()
    statuses = (await db_session.execute(sa.select(Application.match_status))).scalars().all()
    # All 4 apps should now be pending_match
    assert sorted(statuses) == ["pending_match"] * 4
    attempts = (await db_session.execute(sa.select(Application.match_attempts))).scalars().all()
    assert all(a == 0 for a in attempts), f"attempts must reset to 0, got {attempts}"

    # Second audit should find nothing
    rows_after = await match_queue_service.audit_error_apps(db_session)
    assert rows_after == []
