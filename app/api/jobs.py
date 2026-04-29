"""Jobs sync and query endpoints."""

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile
from app.config import Settings, get_settings
from app.database import get_db
from app.models.application import Application
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
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
    user_slugs: list[str] = (profile.target_company_slugs or {}).get("greenhouse", []) or []

    slugs_pending = 0
    invalid_slugs: list[str] = []
    if user_slugs:
        rows = (
            (
                await session.execute(
                    select(SlugFetch).where(
                        SlugFetch.source == "greenhouse_board",
                        SlugFetch.slug.in_(user_slugs),
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            if r.is_invalid:
                invalid_slugs.append(r.slug)
            elif r.queued_at is not None:
                slugs_pending += 1

    matches_pending = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Application)
                .where(
                    Application.profile_id == profile.id,
                    Application.match_status == "pending_match",
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

    return {
        "state": state,
        "slugs_total": len(user_slugs),
        "slugs_pending": slugs_pending,
        "matches_pending": matches_pending,
        "last_sync_requested_at": profile.last_sync_requested_at.isoformat()
        if profile.last_sync_requested_at
        else None,
        "last_sync_completed_at": profile.last_sync_completed_at.isoformat()
        if profile.last_sync_completed_at
        else None,
        "last_sync_summary": profile.last_sync_summary,
        "invalid_slugs": sorted(invalid_slugs),
    }
