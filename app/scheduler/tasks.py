"""
Async task functions called by /internal/cron/* HTTP endpoints (GitHub Actions cron).

Three tasks:
  run_job_sync      — sync + match for all active profiles
  run_generation_queue — generate materials for pending applications
  run_daily_maintenance — staleness cleanup + search auto-pause
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlmodel import select

log = structlog.get_logger()


async def run_job_sync() -> dict:
    """Bulk sweep: prune invalid slugs + enqueue stale slugs for every active
    profile. The actual fetch happens in run_sync_queue; matching for
    cron-discovered jobs happens in run_match_queue.

    Cron MUST go through prune_and_enqueue (not sync_profile) — synchronous
    LLM scoring across N profiles inside Cloud Run's 300s wall caused the
    recurring HTTP 504 in /internal/cron/sync (issue #70). The two-function
    split was finalised in #80.
    """
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services.job_sync_service import prune_and_enqueue

    factory = get_session_factory()
    profiles_enqueued = 0
    slugs_enqueued = 0
    slugs_pruned = 0
    async with factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        for profile in result.scalars().all():
            summary = await prune_and_enqueue(profile, session)
            if summary["queued_slugs"]:
                profiles_enqueued += 1
                slugs_enqueued += len(summary["queued_slugs"])
            slugs_pruned += len(summary["pruned_slugs"])
    return {
        "profiles_enqueued": profiles_enqueued,
        "slugs_enqueued": slugs_enqueued,
        "slugs_pruned": slugs_pruned,
    }


async def run_generation_queue(*, deadline_seconds: int = 240) -> dict:
    """Generate materials for applications stuck in pending status.

    Bounded by `deadline_seconds` per Cloud Run's 300s wall: 10 pending apps ×
    ~10-30s per generate_materials call easily exceeds 300s if Gemini is slow.
    Iterations after the deadline trips are deferred to the next tick — they
    stay in generation_status='pending' and become eligible immediately
    (no lease in this lifecycle, contrast with run_match_queue's 300s lease).

    Note: `generate_materials` is a synchronous-LLM call (no checkpointer).
    The contract was changed in commit 4d47205-era; passing `checkpointer=`
    was a stale kwarg from the LangGraph-interrupt era and TypeError'd at
    every call (silently caught by the generic except, returning failed=N).
    """
    import time

    from app.database import get_session_factory
    from app.models.application import Application
    from app.services.application_service import generate_materials

    factory = get_session_factory()
    deadline = time.monotonic() + deadline_seconds

    async with factory() as session:
        result = await session.execute(
            select(Application)
            .where(
                Application.generation_status.in_(["pending"]),
                Application.generation_attempts < 3,
            )
            .limit(10)
        )
        apps = result.scalars().all()
        app_ids = [a.id for a in apps]

    attempted = len(app_ids)
    succeeded = 0
    failed = 0
    deferred = 0

    for i, app_id in enumerate(app_ids):
        if time.monotonic() > deadline:
            deferred = len(app_ids) - i
            await log.awarning(
                "scheduler.generation_queue_deferred",
                deferred=deferred,
                processed=i,
            )
            break
        try:
            async with factory() as session:
                await generate_materials(app_id, session)
                succeeded += 1
        except Exception as exc:
            failed += 1
            await log.aexception("scheduler.generation_error", app_id=str(app_id), error=str(exc))

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "deferred": deferred,
    }


async def run_daily_maintenance() -> dict:
    """Mark stale jobs + auto-pause expired searches + trim excess matched applications."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services.job_service import mark_stale_jobs

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        stale = await mark_stale_jobs(settings.job_stale_after_days, session)
        await log.ainfo("maintenance.stale_jobs", count=stale)

        # Auto-pause searches that have expired
        result = await session.execute(
            select(UserProfile).where(
                UserProfile.search_active.is_(True),
                UserProfile.search_expires_at.is_not(None),
                UserProfile.search_expires_at < datetime.now(UTC),
            )
        )
        expired_profiles = result.scalars().all()
        for profile in expired_profiles:
            profile.search_active = False
            profile.updated_at = datetime.now(UTC)
            session.add(profile)
            await log.awarning("maintenance.search_paused", profile_id=str(profile.id))
        if expired_profiles:
            await session.commit()
            await log.ainfo("maintenance.searches_paused", count=len(expired_profiles))

        # Trim matched applications to 500 most recent per user
        trim_result = await session.execute(
            text("""
                DELETE FROM applications
                WHERE status = 'matched'
                  AND id NOT IN (
                    SELECT id FROM applications a2
                    WHERE a2.profile_id = applications.profile_id
                      AND a2.status = 'matched'
                    ORDER BY a2.created_at DESC
                    LIMIT 500
                  )
            """)
        )
        await session.commit()
        trimmed = trim_result.rowcount
        if trimmed:
            await log.ainfo("maintenance.applications_trimmed", count=trimmed)

    return {
        "stale_jobs": stale,
        "searches_paused": len(expired_profiles),
        "applications_trimmed": trimmed,
    }


async def run_sync_queue(*, max_slugs: int = 64, deadline_seconds: int = 240) -> dict:
    """Drain the slug fetch queue. Per-tick deadline keeps us under Cloud Run's
    300s wall. Anything not finished is left for the next tick.

    NOTE: http2=True is intentionally NOT set on the httpx client — the `h2`
    package is not a project dependency. Falls back to HTTP/1.1.
    """
    import asyncio
    import time

    import httpx

    from app.database import get_session_factory
    from app.services import job_service, match_queue_service, slug_registry_service
    from app.sources.greenhouse_board import (
        DEFAULT_TIMEOUT,
        GreenhouseBoardSource,
        InvalidSlugError,
        TransientFetchError,
    )

    factory = get_session_factory()
    deadline = time.monotonic() + deadline_seconds
    counts = {"fetched": 0, "invalid": 0, "transient": 0, "skipped_deadline": 0}

    async with factory() as session:
        claimed = await slug_registry_service.next_pending(session, limit=max_slugs)
    if not claimed:
        return {**counts, "remaining": 0}

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        source = GreenhouseBoardSource()
        sem = asyncio.Semaphore(8)

        async def _one(row):
            if time.monotonic() > deadline:
                counts["skipped_deadline"] += 1
                return
            async with sem:
                if time.monotonic() > deadline:
                    counts["skipped_deadline"] += 1
                    return
                # Compute `since`: existing slug → last_fetched_at - 1h overlap;
                # new slug (last_fetched_at IS NULL) → now - 14d.
                since = (
                    row.last_fetched_at - timedelta(hours=1)
                    if row.last_fetched_at is not None
                    else datetime.now(UTC) - timedelta(days=14)
                )
                try:
                    jobs = await source.fetch_jobs(row.slug, since=since, client=client)
                except InvalidSlugError as exc:
                    async with factory() as s:
                        await slug_registry_service.mark_fetched(
                            row.source, row.slug, "invalid", s, error=str(exc)
                        )
                    counts["invalid"] += 1
                    return
                except TransientFetchError as exc:
                    async with factory() as s:
                        await slug_registry_service.mark_fetched(
                            row.source, row.slug, "transient_error", s, error=str(exc)
                        )
                    counts["transient"] += 1
                    return

                async with factory() as s:
                    new_count = 0
                    for jd in jobs:
                        job, created = await job_service.upsert_job(jd, row.source, s)
                        if created:
                            new_count += 1
                            await match_queue_service.enqueue_for_interested_profiles(job, s)
                    await slug_registry_service.mark_fetched(row.source, row.slug, "ok", s)
                    await log.ainfo(
                        "slug_fetch.ok",
                        source=row.source,
                        slug=row.slug,
                        new_jobs=new_count,
                        total_jobs=len(jobs),
                    )
                    counts["fetched"] += 1

        await asyncio.gather(*(_one(r) for r in claimed), return_exceptions=False)

    async with factory() as session:
        remaining = await slug_registry_service.pending_count(session)
    return {**counts, "remaining": remaining}


async def run_match_queue(
    *,
    batch_size: int = 100,
    deadline_seconds: int | None = None,
    max_per_profile: int | None = None,
) -> dict:
    """Drain pending_match applications. One LangGraph batch per profile per tick
    (the agent fans out internally). Per-tick deadline keeps us under Cloud Run's
    300s wall.

    `deadline_seconds` and `max_per_profile` default to
    `Settings.matching_tick_deadline_seconds` and
    `Settings.matching_max_per_profile_per_tick` respectively (see #77 — env-var
    tunable without a redeploy). Tests can still pass explicit overrides.

    `max_per_profile` caps how many jobs a single profile can own in one
    score_and_match call. Without it, batch_size=100 concentrated on one
    profile + slow Gemini latency can exceed the 240s deadline before any
    inter-profile loop check runs (one-off HTTP 504 in
    /internal/cron/process-match-queue, 2026-05-02). Apps over the cap stay
    pending_match with claimed_at set; the 300s lease in next_batch makes
    them re-eligible the tick after."""
    import time

    from app.agents.llm_safe import BudgetExhausted
    from app.config import get_settings

    settings = get_settings()
    if deadline_seconds is None:
        deadline_seconds = settings.matching_tick_deadline_seconds
    if max_per_profile is None:
        max_per_profile = settings.matching_max_per_profile_per_tick
    from app.database import get_session_factory
    from app.models.application import Application
    from app.models.job import Job
    from app.models.user_profile import UserProfile
    from app.services import match_queue_service
    from app.services.match_service import score_and_match

    factory = get_session_factory()
    deadline = time.monotonic() + deadline_seconds
    succeeded = failed = deferred = 0
    budget_exhausted = False

    async with factory() as session:
        batch = await match_queue_service.next_batch(session, limit=batch_size)
    if not batch:
        return {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "deferred": 0,
            "budget_exhausted": False,
        }
    attempted = len(batch)

    # Group by profile_id; one LangGraph invocation per profile
    by_profile: dict = {}
    for app in batch:
        by_profile.setdefault(app.profile_id, []).append(app)

    for profile_id, apps in by_profile.items():
        if time.monotonic() > deadline:
            deferred += len(apps)
            continue
        # Slice per-profile to bound one score_and_match call's wall time.
        # Apps beyond the cap remain claimed (lease set by next_batch); they
        # become re-eligible after the 300s lease expires (~next tick).
        apps_this_tick = apps[:max_per_profile]
        deferred += len(apps) - len(apps_this_tick)
        async with factory() as session:
            profile = (
                await session.execute(select(UserProfile).where(UserProfile.id == profile_id))
            ).scalar_one()
            jobs = list(
                (
                    await session.execute(
                        select(Job).where(Job.id.in_([a.job_id for a in apps_this_tick]))
                    )
                )
                .scalars()
                .all()
            )

            try:
                await score_and_match(profile, session, jobs=jobs)
            except BudgetExhausted as exc:
                # Gemini quota gone — not the app's fault. Release leases so
                # the next tick re-claims naturally once budget restores.
                # DO NOT increment attempts (that's for real failures and
                # accumulates toward match_status='error', which silently
                # discards perfectly good pending matches during a brief
                # credit outage — see #74).
                await log.awarning(
                    "match_queue.budget_exhausted",
                    resumes_at=exc.resumes_at.isoformat(),
                    apps_released=len(apps_this_tick),
                )
                budget_exhausted = True
                for a in apps_this_tick:
                    await match_queue_service.release_claim(a.id, session)
                # No point trying remaining profiles — same Gemini, same outcome.
                break
            except Exception as exc:
                await log.aexception("match_queue.batch_error", error=str(exc))
                for a in apps_this_tick:
                    await match_queue_service.mark_attempt_failed(a.id, session)
                failed += len(apps_this_tick)
                continue

            for a in apps_this_tick:
                # If it has a score now, mark done. If still no score (rate-limited),
                # increment attempts; will retry next tick.
                refreshed = (
                    await session.execute(select(Application).where(Application.id == a.id))
                ).scalar_one()
                if refreshed.match_score is not None or refreshed.status == "auto_rejected":
                    await match_queue_service.mark_done(refreshed.id, session)
                    succeeded += 1
                else:
                    await match_queue_service.mark_attempt_failed(refreshed.id, session)
                    failed += 1

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "deferred": deferred,
        "budget_exhausted": budget_exhausted,
    }
