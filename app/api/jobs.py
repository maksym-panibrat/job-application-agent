"""Jobs sync and query endpoints."""

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.config import Settings, get_settings
from app.database import get_db
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
