"""Job sync entrypoint.

Two contracts here, by design (#80):

  prune_and_enqueue(profile, session)
    Bulk-cron-safe and HTTP-warm-up: drop slugs marked is_invalid, enqueue
    stale slugs for background fetch, update profile.last_sync_*. Fast (no
    LLM). Returns {queued_slugs, matched_now=0, pruned_slugs}.

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
from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.services import match_service, slug_registry_service

log = structlog.get_logger()


async def _prune_invalid_provider_slugs(profile: UserProfile, session: AsyncSession) -> list[str]:
    """For each Company the profile follows, drop any (provider, slug) entry
    whose SlugFetch is marked is_invalid. If a Company ends up with zero
    providers, flag it unfollowable. Returns 'provider:slug' strings pruned."""
    company_ids = list(profile.target_company_ids or [])
    if not company_ids:
        return []
    companies = (
        (await session.execute(select(Company).where(Company.id.in_(company_ids)))).scalars().all()
    )
    if not companies:
        return []

    pruned: list[str] = []
    for company in companies:
        slugs = company.provider_slugs or {}
        if not slugs:
            continue
        pair_clauses = [and_(SlugFetch.source == p, SlugFetch.slug == s) for p, s in slugs.items()]
        invalid_pairs = (
            (
                await session.execute(
                    select(SlugFetch).where(
                        SlugFetch.is_invalid.is_(True),
                        or_(*pair_clauses),
                    )
                )
            )
            .scalars()
            .all()
        )
        invalid_keys = {(r.source, r.slug) for r in invalid_pairs}
        if not invalid_keys:
            continue
        cleaned = {p: s for p, s in slugs.items() if (p, s) not in invalid_keys}
        for p, s in invalid_keys:
            pruned.append(f"{p}:{s}")
        company.provider_slugs = cleaned
        if not cleaned:
            company.unfollowable = True
            await log.awarning("company.unfollowable", company_id=str(company.id))
        session.add(company)
    if pruned:
        await session.commit()
    return sorted(pruned)


async def prune_and_enqueue(profile: UserProfile, session: AsyncSession) -> dict:
    """Cron-safe profile sync: prune invalid slugs + enqueue stale slugs +
    update last_sync_*. No LLM, no synchronous scoring.

    Returns the same summary shape as `sync_profile` but with `matched_now=0`,
    so callers can treat the two functions interchangeably for telemetry.
    """
    pruned = await _prune_invalid_provider_slugs(profile, session)

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    summary = {
        "queued_slugs": queued,
        "matched_now": 0,
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
    )
    return {"status": "queued", **summary}
