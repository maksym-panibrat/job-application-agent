"""Jobs sync and query endpoints."""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import String, func, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.deps import get_current_profile
from app.config import Settings, get_settings
from app.database import get_db
from app.models.application import Application
from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue
from app.services import job_sync_service
from app.services.rate_limit_service import check_daily_quota

log = structlog.get_logger()
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Daily ceiling on user-initiated /api/jobs/sync calls. Previous value of 1
# made the dashboard button unusable after the first click of the day.
MANUAL_SYNC_DAILY_LIMIT = 25


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manual user-initiated sync: enqueues stale slugs + scores cached jobs.
    Returns 202 immediately. Background fetch + match catches up via cron."""
    if settings.environment == "production":
        await check_daily_quota(profile.user_id, "manual_sync", MANUAL_SYNC_DAILY_LIMIT, session)
    result = await job_sync_service.sync_profile(profile, session)
    return JSONResponse(status_code=202, content=result)


# Polled by the dashboard chip every ~3s while sync/match work is in flight.
# Lives under /api/sync/* so it is not nested under /api/jobs.
sync_router = APIRouter(prefix="/api/sync", tags=["sync"])


@sync_router.get("/status")
async def sync_status(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    # Collect every (source, slug) pair the profile follows by walking the
    # Company rows pointed at by target_company_ids. SlugFetch is keyed by
    # (source, slug), so we query against the full pair set rather than the
    # legacy greenhouse-only flat list.
    company_ids = list(profile.target_company_ids or [])
    pairs: list[tuple[str, str]] = []
    if company_ids:
        companies = (
            (
                await session.execute(
                select(Company.provider_slugs).where(col(Company.id).in_(company_ids))
                )
            )
            .scalars()
            .all()
        )
        for ps in companies:
            for source, slug in (ps or {}).items():
                if isinstance(slug, str) and slug:
                    pairs.append((source, slug))

    slugs_pending = 0
    invalid_slugs: list[str] = []
    if pairs:
        rows = (
            await session.execute(
                select(SlugFetch).where(
                    tuple_(col(SlugFetch.source), col(SlugFetch.slug)).in_(pairs)
                )
            )
        ).scalars().all()
        for r in rows:
            if r.is_invalid:
                invalid_slugs.append(r.slug)
        dedupe_keys = [f"fetch-slug:{provider}:{slug}" for provider, slug in pairs]
        slugs_pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(WorkQueue)
                    .where(
                        WorkQueue.job_type == "fetch-slug",
                        col(WorkQueue.status).in_(("pending", "in_progress")),
                        col(WorkQueue.dedupe_key).in_(dedupe_keys),
                    )
                )
            ).scalar_one()
        )

    matches_pending = int(
        (
            await session.execute(
                select(func.count())
                .select_from(WorkQueue)
                .join(
                    Application,
                    col(WorkQueue.payload)["application_id"].astext
                    == col(Application.id).cast(String),
                )
                .where(
                    WorkQueue.job_type == "match",
                    col(WorkQueue.status).in_(("pending", "in_progress")),
                    Application.profile_id == profile.id,
                )
            )
        ).scalar_one()
    )

    if slugs_pending > 0:
        state = "syncing"
    elif matches_pending > 0:
        state = "matching"
    else:
        state = "idle"

    last_sync_summary = profile.last_sync_summary
    if (
        state == "idle"
        and profile.last_sync_requested_at is not None
        and (
            profile.last_sync_completed_at is None
            or profile.last_sync_completed_at < profile.last_sync_requested_at
        )
    ):
        profile.last_sync_completed_at = datetime.now(UTC)
        if isinstance(last_sync_summary, dict):
            last_sync_summary = {**last_sync_summary, "queued_slugs": []}
            profile.last_sync_summary = last_sync_summary
        session.add(profile)
        await session.commit()

    return {
        "state": state,
        "slugs_total": len(pairs),
        "slugs_pending": slugs_pending,
        "matches_pending": matches_pending,
        "last_sync_requested_at": profile.last_sync_requested_at.isoformat()
        if profile.last_sync_requested_at
        else None,
        "last_sync_completed_at": profile.last_sync_completed_at.isoformat()
        if profile.last_sync_completed_at
        else None,
        "last_sync_summary": last_sync_summary,
        "invalid_slugs": sorted(invalid_slugs),
    }
