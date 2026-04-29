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
    """Sync jobs for all users with active search. Returns a summary dict."""
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services import job_sync_service, match_service

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        profiles = result.scalars().all()

    profiles_synced = 0
    total_new = 0
    total_updated = 0
    total_stale = 0
    total_warnings: dict[str, int] = {}

    for profile in profiles:
        try:
            async with factory() as session:
                sync_result = await job_sync_service.sync_profile(profile, session)
                profiles_synced += 1
                total_new += sync_result.get("new_jobs", 0)
                total_updated += sync_result.get("updated_jobs", 0)
                total_stale += sync_result.get("stale_jobs", 0)
                # Generic aggregation — counts every warning code emitted by
                # sync_profile, so new codes surface automatically (#48).
                for w in sync_result.get("warnings", []):
                    total_warnings[w] = total_warnings.get(w, 0) + 1
                await match_service.score_and_match(profile, session)
        except Exception as exc:
            await log.aexception("scheduler.sync_error", profile_id=str(profile.id), error=str(exc))

    return {
        "profiles_synced": profiles_synced,
        # Back-compat field; total_warnings["no_target_slugs"] is the canonical source.
        "profiles_without_slugs": total_warnings.get("no_target_slugs", 0),
        "total_warnings": total_warnings,
        "total_new_jobs": total_new,
        "total_updated_jobs": total_updated,
        "total_stale_jobs": total_stale,
    }


async def run_generation_queue(checkpointer) -> dict:
    """Generate materials for applications stuck in pending status. Returns a summary dict.

    ``checkpointer`` must be a LangGraph checkpointer (typically
    ``request.app.state.checkpointer`` initialized in the FastAPI lifespan).
    ``generate_materials`` raises ``RuntimeError`` if it is None.
    """
    from app.database import get_session_factory
    from app.models.application import Application
    from app.services.application_service import generate_materials

    factory = get_session_factory()
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

    for app_id in app_ids:
        try:
            async with factory() as session:
                await generate_materials(app_id, session, checkpointer=checkpointer)
                succeeded += 1
        except Exception as exc:
            failed += 1
            await log.aexception("scheduler.generation_error", app_id=str(app_id), error=str(exc))

    return {"attempted": attempted, "succeeded": succeeded, "failed": failed}


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
