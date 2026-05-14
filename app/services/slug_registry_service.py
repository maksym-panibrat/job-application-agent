"""Slug-level fetch state. One row per (source, slug), shared across users."""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.sources import SOURCES

INVALID_THRESHOLD = 2
log = structlog.get_logger()


async def get(source: str, slug: str, session: AsyncSession) -> SlugFetch | None:
    result = await session.execute(
        select(SlugFetch).where(SlugFetch.source == source, SlugFetch.slug == slug)
    )
    return result.scalar_one_or_none()


async def validate_slug(source: str, slug: str, session: AsyncSession) -> bool:
    """Returns True if the slug exists on the given provider's board."""
    adapter = SOURCES.get(source)
    if adapter is None:
        raise ValueError(f"unknown provider: {source}")
    ok = await adapter.validate(slug)
    if not ok:
        return False
    stmt = insert(SlugFetch).values(source=source, slug=slug).on_conflict_do_nothing()
    await session.execute(stmt)
    await session.commit()
    return True


async def mark_fetched(
    source: str,
    slug: str,
    status: str,
    session: AsyncSession,
    *,
    error: str | None = None,
) -> SlugFetch:
    """Record a fetch outcome. status ∈ {'ok','invalid','transient_error'}."""
    now = datetime.now(UTC)
    row = await get(source, slug, session)
    if row is None:
        row = SlugFetch(source=source, slug=slug)
        session.add(row)

    row.last_attempted_at = now
    if status == "ok":
        row.last_fetched_at = now
        row.consecutive_404_count = 0
        row.consecutive_5xx_count = 0
    elif status == "invalid":
        row.consecutive_404_count += 1
        row.consecutive_5xx_count = 0
        if row.consecutive_404_count >= INVALID_THRESHOLD:
            row.is_invalid = True
            row.invalid_reason = error or "Greenhouse returned 404 (board not found)"
            await log.awarning(
                "slug_registry.invalidated",
                source=source,
                slug=slug,
                count=row.consecutive_404_count,
            )
    elif status == "transient_error":
        row.consecutive_5xx_count += 1
    else:
        raise ValueError(f"unknown status: {status}")

    await session.commit()
    await session.refresh(row)
    return row


async def list_stale_for_profile(
    profile,
    session: AsyncSession,
    *,
    ttl_hours: int = 6,
) -> list[tuple[str, str]]:
    company_ids = list(profile.target_company_ids or [])
    if not company_ids:
        return []
    companies = (
        (
            await session.execute(
                select(Company).where(
                    col(Company.id).in_(company_ids),
                    col(Company.unfollowable).is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    if not companies:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for company in companies:
        for provider, slug in (company.provider_slugs or {}).items():
            key = (provider, slug)
            if key in seen:
                continue
            row = await get(provider, slug, session)
            if row is not None and row.is_invalid:
                continue
            is_stale = row is None or row.last_fetched_at is None or row.last_fetched_at < cutoff
            if is_stale:
                pairs.append(key)
                seen.add(key)
    return pairs


async def prune_invalid_for_profile(profile, session: AsyncSession) -> int:
    company_ids = list(profile.target_company_ids or [])
    if not company_ids:
        return 0
    companies = (
        (
            await session.execute(
                select(Company).where(col(Company.id).in_(company_ids))
            )
        )
        .scalars()
        .all()
    )
    if not companies:
        return 0

    pruned = 0
    for company in companies:
        slugs = company.provider_slugs or {}
        if not slugs:
            continue
        pair_clauses = [and_(SlugFetch.source == p, SlugFetch.slug == s) for p, s in slugs.items()]
        invalid_pairs = (
            (
                await session.execute(
                    select(SlugFetch).where(
                        col(SlugFetch.is_invalid).is_(True),
                        or_(*pair_clauses),
                    )
                )
            )
            .scalars()
            .all()
        )
        invalid_keys = {(row.source, row.slug) for row in invalid_pairs}
        if not invalid_keys:
            continue
        company.provider_slugs = {
            provider: slug
            for provider, slug in slugs.items()
            if (provider, slug) not in invalid_keys
        }
        if not company.provider_slugs:
            company.unfollowable = True
            await log.awarning("company.unfollowable", company_id=str(company.id))
        session.add(company)
        pruned += len(invalid_keys)
    return pruned
