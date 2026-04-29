"""Job sync entrypoint — enqueueing-only, fast.

The actual fetch happens in app.scheduler.tasks.run_sync_queue.
The actual matching happens in app.scheduler.tasks.run_match_queue.
This function:
  1. Seeds 5 default slugs if profile has none.
  2. Enqueues every stale (last_fetched_at NULL or > 6h old) slug for background fetch.
  3. Scores up to `matching_jobs_per_batch` already-cached, slug-scoped, unscored jobs
     so the user sees something immediately.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user_profile import UserProfile
from app.services import match_service, slug_registry_service
from app.services.profile_service import seed_defaults_if_empty

log = structlog.get_logger()


async def sync_profile(profile: UserProfile, session: AsyncSession) -> dict:
    settings = get_settings()
    seeded = seed_defaults_if_empty(profile)
    if seeded:
        session.add(profile)
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    matched = await match_service.score_cached(
        profile, session, cap=settings.matching_jobs_per_batch
    )

    summary = {
        "queued_slugs": queued,
        "matched_now": len(matched),
        "seeded_defaults": seeded,
    }
    profile.last_sync_requested_at = datetime.now(UTC)
    profile.last_sync_summary = summary
    if not queued:
        # Nothing to fetch — sync is "complete" right now.
        profile.last_sync_completed_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()

    await log.ainfo(
        "sync.queued",
        profile_id=str(profile.id),
        queued_slugs=queued,
        matched_now=len(matched),
        seeded_defaults=seeded,
    )
    return {"status": "queued", **summary}
