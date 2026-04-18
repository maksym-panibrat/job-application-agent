"""Jobs sync and query endpoints."""

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.user_profile import UserProfile
from app.services import job_sync_service

log = structlog.get_logger()
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/sync")
async def trigger_sync(
    background_tasks: BackgroundTasks,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """
    Manually trigger a job sync for the current user.
    In dev mode: runs inline and returns results.
    In prod: would run via scheduler.
    """
    result = await job_sync_service.sync_profile(profile, session)

    # After sync, score new jobs
    background_tasks.add_task(_score_after_sync, profile.user_id)

    return {"status": "synced", **result}


async def _score_after_sync(profile_id):
    """Background task: score jobs after sync completes."""
    from app.database import get_session_factory
    from app.services.match_service import score_and_match
    from app.services.profile_service import get_or_create_profile

    factory = get_session_factory()
    async with factory() as session:
        profile = await get_or_create_profile(profile_id, session)
        await score_and_match(profile, session)
