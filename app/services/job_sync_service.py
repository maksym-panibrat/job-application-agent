"""Job sync entrypoint — enqueueing-only, fast.

The actual fetch happens in app.scheduler.tasks.run_sync_queue.
The actual matching happens in app.scheduler.tasks.run_match_queue.
This function:
  1. Seeds 5 default slugs if profile has none.
  2. Drops any slug whose SlugFetch row is is_invalid=True (the source of
     the "We removed X" banner — backend now matches the UI promise).
  3. Enqueues every stale (last_fetched_at NULL or > 6h old) slug for background fetch.
  4. Scores up to `matching_jobs_per_batch` already-cached, slug-scoped, unscored jobs
     so the user sees something immediately.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.services import match_service, slug_registry_service
from app.services.profile_service import seed_defaults_if_empty

log = structlog.get_logger()


async def _prune_invalid_slugs(profile: UserProfile, session: AsyncSession) -> list[str]:
    """Drop greenhouse slugs marked is_invalid=True from the profile. Returns
    the list of removed slugs (sorted, possibly empty). Caller commits."""
    user_slugs = (profile.target_company_slugs or {}).get("greenhouse") or []
    if not user_slugs:
        return []
    rows = (
        (
            await session.execute(
                select(SlugFetch).where(
                    SlugFetch.source == "greenhouse_board",
                    SlugFetch.slug.in_(user_slugs),
                    SlugFetch.is_invalid.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    invalid = {r.slug for r in rows}
    if not invalid:
        return []
    cleaned = [s for s in user_slugs if s not in invalid]
    profile.target_company_slugs = {
        **(profile.target_company_slugs or {}),
        "greenhouse": cleaned,
    }
    session.add(profile)
    return sorted(invalid)


async def sync_profile(profile: UserProfile, session: AsyncSession) -> dict:
    settings = get_settings()
    seeded = seed_defaults_if_empty(profile)
    if seeded:
        session.add(profile)
        await session.commit()

    pruned = await _prune_invalid_slugs(profile, session)
    if pruned:
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    matched = await match_service.score_cached(
        profile, session, cap=settings.matching_jobs_per_batch
    )

    summary = {
        "queued_slugs": queued,
        "matched_now": len(matched),
        "seeded_defaults": seeded,
        "pruned_slugs": pruned,
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
