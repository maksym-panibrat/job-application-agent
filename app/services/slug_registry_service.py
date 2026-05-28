"""Slug-level fetch state. One row per (source, slug), shared across users."""

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.company import Company
from app.models.slug_fetch import SlugFetch

INVALID_THRESHOLD = 2
log = structlog.get_logger()


async def get(source: str, slug: str, session: AsyncSession) -> SlugFetch | None:
    result = await session.execute(
        select(SlugFetch).where(SlugFetch.source == source, SlugFetch.slug == slug)
    )
    return result.scalar_one_or_none()


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


async def list_stale_for_active_profiles(
    session: AsyncSession,
    *,
    ttl_hours: int = 6,
) -> list[tuple[uuid.UUID, str, str]]:
    """Return stale provider slugs by active profile without per-slug lookups."""
    result = await session.execute(
        sa.text(
            """
            WITH active_companies AS (
              SELECT id AS profile_id, unnest(target_company_ids) AS company_id
              FROM user_profiles
              WHERE search_active IS TRUE
                AND target_company_ids IS NOT NULL
            ),
            provider_slugs AS (
              SELECT DISTINCT
                     ac.profile_id,
                     kv.key::text AS source,
                     kv.value::text AS slug
              FROM active_companies ac
              JOIN companies c ON c.id = ac.company_id
              CROSS JOIN LATERAL jsonb_each_text(c.provider_slugs) AS kv(key, value)
              WHERE c.unfollowable IS FALSE
                AND kv.value IS NOT NULL
                AND kv.value <> ''
            )
            SELECT ps.profile_id, ps.source, ps.slug
            FROM provider_slugs ps
            LEFT JOIN slug_fetches sf
              ON sf.source = ps.source
             AND sf.slug = ps.slug
            WHERE COALESCE(sf.is_invalid, false) IS FALSE
              AND (
                sf.source IS NULL
                OR sf.last_fetched_at IS NULL
                OR sf.last_fetched_at < now() - make_interval(hours => :ttl_hours)
              )
            ORDER BY ps.profile_id, ps.source, ps.slug
            """
        ),
        {"ttl_hours": ttl_hours},
    )
    return [(row[0], row[1], row[2]) for row in result.all()]
