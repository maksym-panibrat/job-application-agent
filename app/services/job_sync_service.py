"""Job sync entrypoint.

Two contracts here, by design (#80):

  prune_and_enqueue(profile, session)
    Bulk-cron-safe and HTTP-warm-up: drop slugs marked is_invalid, enqueue stale
    provider slugs into work_queue, update profile.last_sync_*. Fast (no LLM).
    Returns {queued_slugs, matched_now=0, pruned_slugs}.

  sync_profile(profile, session)
    User-triggered HTTP path (POST /api/jobs/sync). Calls prune_and_enqueue
    only. Matching is handled by the always-on worker so the deterministic
    pre-LLM filters are applied consistently.

The actual fetch and matching happen in the always-on worker.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.config import get_settings
from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.services import slug_registry_service
from app.worker.payloads import FetchSlugPayload
from app.worker.queue_service import enqueue

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
    settings = get_settings()
    pruned = await _prune_invalid_provider_slugs(profile, session)

    stale = await slug_registry_service.list_stale_for_profile(profile, session, ttl_hours=6)
    queued: list[str] = []
    for provider, slug in stale:
        row_id = await enqueue(
            session,
            job_type="fetch-slug",
            payload=FetchSlugPayload(
                provider=provider,
                slug=slug,
                batch_match_max_items=settings.batch_match_manual_sync_max_items,
            ).model_dump(exclude_none=True),
            dedupe_key=f"fetch-slug:{provider}:{slug}",
        )
        if row_id is not None:
            queued.append(slug)
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


async def sync_active_profiles(session: AsyncSession) -> dict:
    """Cron/scheduler sweep: enqueue distinct stale provider slugs for active profiles."""
    settings = get_settings()
    active_profiles = (
        (
            await session.execute(
                select(UserProfile).where(col(UserProfile.search_active).is_(True))
            )
        )
        .scalars()
        .all()
    )

    pruned_by_profile: dict[uuid.UUID, list[str]] = {}
    for profile in active_profiles:
        pruned_by_profile[profile.id] = await _prune_invalid_provider_slugs(profile, session)

    stale_by_profile = await slug_registry_service.list_stale_for_active_profiles(
        session,
        ttl_hours=6,
    )

    pairs_by_profile: dict[uuid.UUID, list[tuple[str, str]]] = {}
    distinct_pairs: dict[tuple[str, str], str] = {}
    for profile_id, provider, slug in stale_by_profile:
        pairs_by_profile.setdefault(profile_id, []).append((provider, slug))
        distinct_pairs.setdefault((provider, slug), slug)

    enqueued: list[str] = []
    enqueued_pairs: set[tuple[str, str]] = set()
    for (provider, slug), display_slug in distinct_pairs.items():
        row_id = await enqueue(
            session,
            job_type="fetch-slug",
            payload=FetchSlugPayload(
                provider=provider,
                slug=slug,
                batch_match_max_items=settings.batch_match_cron_max_items,
            ).model_dump(exclude_none=True),
            dedupe_key=f"fetch-slug:{provider}:{slug}",
        )
        if row_id is not None:
            enqueued.append(display_slug)
            enqueued_pairs.add((provider, slug))

    now = datetime.now(UTC)
    profiles_enqueued = 0
    for profile in active_profiles:
        profile_slugs = [
            slug
            for provider, slug in pairs_by_profile.get(profile.id, [])
            if (provider, slug) in enqueued_pairs
        ]
        profile.last_sync_requested_at = now
        profile.last_sync_summary = {
            "queued_slugs": profile_slugs,
            "matched_now": 0,
            "pruned_slugs": pruned_by_profile.get(profile.id, []),
        }
        if profile_slugs:
            profiles_enqueued += 1
        else:
            profile.last_sync_completed_at = now
        session.add(profile)

    await session.commit()

    return {
        "enqueued": enqueued,
        "pruned": sum(len(values) for values in pruned_by_profile.values()),
        "active_profiles": len(active_profiles),
        "profiles_enqueued": profiles_enqueued,
    }


async def sync_profile(profile: UserProfile, session: AsyncSession) -> dict:
    """User-triggered HTTP path: prune + enqueue only.

    The worker drains fetch and match work after the 202 response.
    """
    summary = await prune_and_enqueue(profile, session)
    await log.ainfo(
        "sync.queued",
        profile_id=str(profile.id),
        queued_slugs=summary["queued_slugs"],
        matched_now=summary["matched_now"],
    )
    return {"status": "queued", **summary}
