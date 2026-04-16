"""
APScheduler tasks — only run in production (ENVIRONMENT=production).

Three jobs:
  run_job_sync      — every 24h: sync + match for all active profiles
  run_generation_queue — every 5min: generate materials for pending applications
  run_daily_maintenance — 03:00 cron: staleness cleanup + search auto-pause
"""

from datetime import UTC, datetime

import structlog
from sqlmodel import select

log = structlog.get_logger()


async def run_job_sync() -> None:
    """Sync jobs for all users with active search."""
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services import job_sync_service, match_service

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        profiles = result.scalars().all()

    for profile in profiles:
        try:
            async with factory() as session:
                await job_sync_service.sync_profile(profile, session)
                await match_service.score_and_match(profile, session)
        except Exception as exc:
            await log.aexception("scheduler.sync_error", profile_id=str(profile.id), error=str(exc))
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:
                pass


async def run_generation_queue() -> None:
    """Generate materials for applications stuck in pending/generating status."""
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

    for app_id in app_ids:
        try:
            async with factory() as session:
                await generate_materials(app_id, session)
        except Exception as exc:
            await log.aexception(
                "scheduler.generation_error", app_id=str(app_id), error=str(exc)
            )


async def run_daily_maintenance() -> None:
    """Mark stale jobs + auto-pause expired searches."""
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


def setup_scheduler(app) -> None:
    """Initialize APScheduler and attach to FastAPI app state."""
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.config import get_settings

    settings = get_settings()
    # APScheduler SQLAlchemyJobStore requires sync URL
    sync_db_url = str(settings.database_url).replace("+asyncpg", "")

    jobstores = {"default": SQLAlchemyJobStore(url=sync_db_url)}
    scheduler = AsyncIOScheduler(jobstores=jobstores)

    scheduler.add_job(
        run_job_sync,
        "interval",
        hours=settings.job_sync_interval_hours,
        id="run_job_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_generation_queue,
        "interval",
        minutes=5,
        id="run_generation_queue",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_maintenance,
        "cron",
        hour=3,
        minute=0,
        id="run_daily_maintenance",
        replace_existing=True,
    )

    scheduler.start()
    app.state.scheduler = scheduler
    return scheduler
