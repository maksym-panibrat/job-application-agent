"""Job sync entrypoint.

Two contracts here, by design (#80):

  prune_and_enqueue(profile, session)
    Bulk-cron-safe and HTTP-warm-up: seed defaults if the profile is empty,
    drop slugs marked is_invalid, enqueue stale slugs for background fetch,
    update profile.last_sync_*. Fast (no LLM). Returns
    {queued_slugs, matched_now=0, seeded_defaults, pruned_slugs}.

  sync_profile(profile, session)
    User-triggered HTTP path (POST /api/jobs/sync). Calls prune_and_enqueue
    then synchronously scores up to `matching_jobs_per_batch` already-cached
    jobs for instant UI feedback. Cron MUST NOT call this — Cloud Run's 300s
    wall + N-profile fan-out blew up the synchronous scoring path (#70 /
    commit 191df6a regression / fixed by #71).

The actual fetch happens in app.scheduler.tasks.run_sync_queue.
The actual matching happens in app.scheduler.tasks.run_match_queue.
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
                    SlugFetch.source == "greenhouse",
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


async def prune_and_enqueue(profile: UserProfile, session: AsyncSession) -> dict:
    """Cron-safe profile sync: seed defaults + prune invalid slugs + enqueue
    stale slugs + update last_sync_*. No LLM, no synchronous scoring.

    Returns the same summary shape as `sync_profile` but with `matched_now=0`,
    so callers can treat the two functions interchangeably for telemetry.
    """
    seeded = seed_defaults_if_empty(profile)
    if seeded:
        session.add(profile)
        await session.commit()

    pruned = await _prune_invalid_slugs(profile, session)
    if pruned:
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    summary = {
        "queued_slugs": queued,
        "matched_now": 0,
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
    return summary


async def sync_profile(profile: UserProfile, session: AsyncSession) -> dict:
    """User-triggered HTTP path: prune + enqueue, then synchronously LLM-score
    up to N cached jobs for instant UI feedback. Cron MUST NOT call this — see
    module docstring."""
    settings = get_settings()
    summary = await prune_and_enqueue(profile, session)
    matched = await match_service.score_cached(
        profile, session, cap=settings.matching_jobs_per_batch
    )
    if matched:
        summary["matched_now"] = len(matched)
        profile.last_sync_summary = summary
        session.add(profile)
        await session.commit()

    await log.ainfo(
        "sync.queued",
        profile_id=str(profile.id),
        queued_slugs=summary["queued_slugs"],
        matched_now=summary["matched_now"],
        seeded_defaults=summary["seeded_defaults"],
    )
    return {"status": "queued", **summary}
