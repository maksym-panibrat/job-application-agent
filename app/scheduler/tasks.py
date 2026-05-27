"""Async maintenance helpers used by cron enqueuers and worker handlers."""

from datetime import UTC, datetime, timedelta

import structlog
from sqlmodel import col, select

log = structlog.get_logger()


async def run_job_sync() -> dict:
    """Bulk sweep: prune invalid slugs + enqueue stale slugs for active profiles."""
    from app.database import get_session_factory
    from app.services.job_sync_service import sync_active_profiles

    factory = get_session_factory()
    async with factory() as session:
        summary = await sync_active_profiles(session)
    return {
        "profiles_enqueued": summary["profiles_enqueued"],
        "slugs_enqueued": len(summary["enqueued"]),
        "slugs_pruned": summary["pruned"],
    }


async def run_daily_maintenance() -> dict:
    """Mark stale jobs + auto-pause expired searches + trim excess matched applications."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services.entitlements import (
        effective_entitlements,
        get_subscription_snapshot,
        latest_engagement_since,
        next_search_expiry,
        record_entitlement_decision,
    )
    from app.services.job_service import mark_stale_jobs

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        stale = await mark_stale_jobs(settings.job_stale_after_days, session)
        await log.ainfo("maintenance.stale_jobs", count=stale)

        # Reconcile active search expiry windows.
        now = datetime.now(UTC)
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        searches_paused = 0
        searches_extended = 0
        for profile in result.scalars().all():
            subscription = await get_subscription_snapshot(profile.user_id, session)
            entitlements = effective_entitlements(subscription, now=now)
            previous_value = {
                "search_active": profile.search_active,
                "search_expires_at": (
                    profile.search_expires_at.isoformat()
                    if profile.search_expires_at is not None
                    else None
                ),
            }

            if entitlements.paid_access:
                next_expiry = next_search_expiry(now, settings)
                profile.search_active = True
                profile.search_expires_at = next_expiry
                profile.updated_at = now
                session.add(profile)
                await record_entitlement_decision(
                    session,
                    user_id=profile.user_id,
                    profile_id=profile.id,
                    decision_type="search_expiry_extended",
                    reason="paid_entitlement",
                    previous_value=previous_value,
                    next_value={
                        "search_active": True,
                        "search_expires_at": next_expiry.isoformat(),
                    },
                )
                searches_extended += 1
                await log.ainfo(
                    "maintenance.search_extended",
                    profile_id=str(profile.id),
                    user_id=str(profile.user_id),
                    reason="paid_entitlement",
                )
                continue

            if profile.search_expires_at is None:
                next_expiry = next_search_expiry(now, settings)
                profile.search_expires_at = next_expiry
                profile.updated_at = now
                session.add(profile)
                await record_entitlement_decision(
                    session,
                    user_id=profile.user_id,
                    profile_id=profile.id,
                    decision_type="search_expiry_seeded",
                    reason="missing_expiry",
                    previous_value=previous_value,
                    next_value={
                        "search_active": True,
                        "search_expires_at": next_expiry.isoformat(),
                    },
                )
                searches_extended += 1
                await log.ainfo(
                    "maintenance.search_expiry_seeded",
                    profile_id=str(profile.id),
                    user_id=str(profile.user_id),
                )
                continue

            window_start = profile.search_expires_at - timedelta(
                days=settings.search_auto_pause_days
            )
            engagement = await latest_engagement_since(
                session,
                profile_id=profile.id,
                since=window_start,
            )
            if engagement is not None:
                next_expiry = next_search_expiry(now, settings)
                profile.search_expires_at = next_expiry
                profile.updated_at = now
                session.add(profile)
                await record_entitlement_decision(
                    session,
                    user_id=profile.user_id,
                    profile_id=profile.id,
                    decision_type="search_expiry_extended",
                    reason="active_engagement",
                    previous_value=previous_value,
                    next_value={
                        "search_active": True,
                        "search_expires_at": next_expiry.isoformat(),
                    },
                    source_event_type=engagement.event_type,
                    source_event_id=engagement.id,
                )
                searches_extended += 1
                await log.ainfo(
                    "maintenance.search_extended",
                    profile_id=str(profile.id),
                    user_id=str(profile.user_id),
                    reason="active_engagement",
                    source_event_id=str(engagement.id),
                )
                continue

            if profile.search_expires_at < now:
                profile.search_active = False
                profile.updated_at = now
                session.add(profile)
                await record_entitlement_decision(
                    session,
                    user_id=profile.user_id,
                    profile_id=profile.id,
                    decision_type="search_paused",
                    reason="inactivity",
                    previous_value=previous_value,
                    next_value={
                        "search_active": False,
                        "search_expires_at": profile.search_expires_at.isoformat(),
                    },
                )
                searches_paused += 1
                await log.awarning(
                    "maintenance.search_paused",
                    profile_id=str(profile.id),
                    user_id=str(profile.user_id),
                )
        if searches_paused or searches_extended:
            await session.commit()
            await log.ainfo(
                "maintenance.searches_reconciled",
                paused=searches_paused,
                extended=searches_extended,
            )

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

        # Events retention — delete > 90 days old (spec section 7).
        cutoff = datetime.now(UTC) - timedelta(days=90)
        events_result = await session.execute(
            text("DELETE FROM events WHERE occurred_at < :cutoff"),
            {"cutoff": cutoff},
        )
        await session.commit()
        events_deleted = events_result.rowcount
        if events_deleted:
            await log.ainfo("maintenance.events_deleted", count=events_deleted)

        done_pruned = (
            await session.execute(
                text(
                    "DELETE FROM work_queue WHERE status = 'done' "
                    "AND completed_at < now() - interval '7 days'"
                )
            )
        ).rowcount
        failed_pruned = (
            await session.execute(
                text(
                    "DELETE FROM work_queue WHERE status = 'failed' "
                    "AND completed_at < now() - interval '30 days'"
                )
            )
        ).rowcount
        await session.commit()
        if done_pruned or failed_pruned:
            await log.ainfo(
                "maintenance.work_queue_pruned",
                done=done_pruned,
                failed=failed_pruned,
            )

    return {
        "stale_jobs": stale,
        "searches_paused": searches_paused,
        "applications_trimmed": trimmed,
        "events_deleted": events_deleted,
        "work_queue_done_pruned": done_pruned,
        "work_queue_failed_pruned": failed_pruned,
    }


async def fetch_one_slug(*, provider: str, slug: str, session_factory) -> dict:
    """Fetch one provider slug, upsert jobs, and enqueue match work_queue rows."""
    import httpx

    from app.models.application import Application
    from app.services import job_service, slug_registry_service
    from app.sources import SOURCES
    from app.sources.base import InvalidSlugError, TransientFetchError
    from app.sources.greenhouse_board import DEFAULT_TIMEOUT
    from app.worker.queue_service import enqueue

    adapter = SOURCES.get(provider)
    if adapter is None:
        await log.aerror("slug_fetch.unknown_provider", source=provider, slug=slug)
        async with session_factory() as session:
            await slug_registry_service.mark_fetched(
                provider,
                slug,
                "transient_error",
                session,
                error="unknown provider",
            )
        return {
            "status": "transient",
            "new_jobs": 0,
            "total_jobs": 0,
            "matches_enqueued": 0,
        }

    async with session_factory() as session:
        slug_row = await slug_registry_service.get(provider, slug, session)
        last_fetched_at = slug_row.last_fetched_at if slug_row is not None else None
    since = (
        last_fetched_at - timedelta(hours=1)
        if last_fetched_at is not None
        else datetime.now(UTC) - timedelta(days=14)
    )

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            jobs = await adapter.fetch_jobs(slug, since=since, client=client)
    except InvalidSlugError as exc:
        async with session_factory() as session:
            await slug_registry_service.mark_fetched(
                provider, slug, "invalid", session, error=str(exc)
            )
        return {
            "status": "invalid",
            "new_jobs": 0,
            "total_jobs": 0,
            "matches_enqueued": 0,
        }
    except TransientFetchError as exc:
        async with session_factory() as session:
            await slug_registry_service.mark_fetched(
                provider, slug, "transient_error", session, error=str(exc)
            )
        raise

    new_count = 0
    matches_enqueued = 0
    async with session_factory() as session:
        for jd in jobs:
            job, created = await job_service.upsert_job(jd, provider, session, slug=slug)
            if created:
                new_count += 1
            await _create_applications_for_interested_profiles(job, session)
            apps = (
                (
                    await session.execute(
                        select(Application).where(
                            Application.job_id == job.id,
                            col(Application.match_score).is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for app in apps:
                await enqueue(
                    session,
                    job_type="match",
                    payload={"application_id": str(app.id)},
                    dedupe_key=f"match:{app.id}",
                )
                matches_enqueued += 1
        await slug_registry_service.mark_fetched(provider, slug, "ok", session)
        await session.commit()

    await log.ainfo(
        "slug_fetch.ok",
        source=provider,
        slug=slug,
        new_jobs=new_count,
        total_jobs=len(jobs),
        matches_enqueued=matches_enqueued,
    )
    return {
        "status": "ok",
        "new_jobs": new_count,
        "total_jobs": len(jobs),
        "matches_enqueued": matches_enqueued,
    }


async def _create_applications_for_interested_profiles(job, session) -> int:
    import uuid

    from sqlalchemy.dialects.postgresql import insert

    from app.data.slug_company import company_name_to_slug
    from app.models.application import Application
    from app.models.company import Company
    from app.models.user_profile import UserProfile

    company_id = job.company_id
    if company_id is None:
        slug = company_name_to_slug(job.company_name)
        resolved = await session.execute(
            select(Company.id).where(Company.provider_slugs[job.source].astext == slug)
        )
        company_id = resolved.scalar_one_or_none()
        if company_id is None:
            return 0

    result = await session.execute(
        select(UserProfile.id).where(
            UserProfile.search_active.is_(True),
            col(UserProfile.target_company_ids).contains([company_id]),
        )
    )
    profile_ids = [row[0] for row in result.all()]
    if not profile_ids:
        return 0

    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid.uuid4(),
            "job_id": job.id,
            "profile_id": profile_id,
            "status": "pending_review",
            "generation_status": "none",
            "generation_attempts": 0,
            "match_strengths": [],
            "match_gaps": [],
            "created_at": now,
            "updated_at": now,
        }
        for profile_id in profile_ids
    ]
    stmt = (
        insert(Application)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_applications_job_profile")
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
