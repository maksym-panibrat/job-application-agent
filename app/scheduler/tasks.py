"""
Async task functions called by /internal/cron/* HTTP endpoints (GitHub Actions cron).

Three tasks:
  run_job_sync      — sync + match for all active profiles
  run_generation_queue — generate materials for pending applications
  run_daily_maintenance — staleness cleanup + search auto-pause
"""

from datetime import UTC, datetime

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

    for profile in profiles:
        try:
            async with factory() as session:
                sync_result = await job_sync_service.sync_profile(profile, session)
                profiles_synced += 1
                total_new += sync_result.get("new_jobs", 0)
                total_updated += sync_result.get("updated_jobs", 0)
                total_stale += sync_result.get("stale_jobs", 0)
                await match_service.score_and_match(profile, session)
        except Exception as exc:
            await log.aexception("scheduler.sync_error", profile_id=str(profile.id), error=str(exc))
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:
                pass

    return {
        "profiles_synced": profiles_synced,
        "total_new_jobs": total_new,
        "total_updated_jobs": total_updated,
        "total_stale_jobs": total_stale,
    }


async def run_generation_queue() -> dict:
    """Generate materials for applications stuck in pending status. Returns a summary dict."""
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
                await generate_materials(app_id, session)
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
