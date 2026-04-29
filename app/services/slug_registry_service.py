"""Slug-level fetch state. One row per (source, slug), shared across users.

Lifecycle:
  validate_slug   → writes row with last_status='ok' (no fetch yet)
  enqueue_stale   → sets queued_at on existing row (or inserts then sets)
  next_pending    → claims rows by setting claimed_at
  mark_fetched    → updates last_status, counters, clears queued_at + claimed_at,
                    flips is_invalid after 2 consecutive 404s
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.slug_fetch import SlugFetch
from app.sources.greenhouse_board import GreenhouseBoardSource

INVALID_THRESHOLD = 2
log = structlog.get_logger()


async def get(source: str, slug: str, session: AsyncSession) -> SlugFetch | None:
    result = await session.execute(
        select(SlugFetch).where(SlugFetch.source == source, SlugFetch.slug == slug)
    )
    return result.scalar_one_or_none()


async def validate_slug(source: str, slug: str, session: AsyncSession) -> bool:
    """Returns True if the slug exists on Greenhouse. On True, upserts a row
    with last_status='ok' (no last_fetched_at — that's set by an actual fetch)."""
    if source != "greenhouse_board":
        raise ValueError(f"validate_slug only supports greenhouse_board (got {source})")
    src = GreenhouseBoardSource()
    ok = await src.validate(slug)
    if not ok:
        return False
    stmt = (
        insert(SlugFetch)
        .values(source=source, slug=slug, last_status="ok")
        .on_conflict_do_update(
            index_elements=["source", "slug"],
            set_={"last_status": "ok"},
        )
    )
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
    row.last_status = status
    row.queued_at = None
    row.claimed_at = None

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


async def enqueue_stale(profile, session: AsyncSession, *, ttl_hours: int = 6) -> list[str]:
    """For each greenhouse slug on the profile that's not invalid:
    if its last_fetched_at is NULL or older than now-ttl_hours, set queued_at=now().
    Returns the list of slugs newly queued (excluding ones already queued)."""
    slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
    if not slugs:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    queued: list[str] = []
    for slug in slugs:
        row = await get("greenhouse_board", slug, session)
        if row is None:
            row = SlugFetch(source="greenhouse_board", slug=slug, queued_at=datetime.now(UTC))
            session.add(row)
            queued.append(slug)
            continue
        if row.is_invalid:
            continue
        already_queued = row.queued_at is not None
        is_stale = row.last_fetched_at is None or row.last_fetched_at < cutoff
        if is_stale and not already_queued:
            row.queued_at = datetime.now(UTC)
            queued.append(slug)
    await session.commit()
    return queued


async def next_pending(
    session: AsyncSession, *, limit: int, lease_seconds: int = 300
) -> list[SlugFetch]:
    """Claim up to `limit` queued rows. A row is claimable if queued_at is set
    and (claimed_at is NULL or older than lease_seconds ago).
    Selected rows have claimed_at set to now() before return."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    result = await session.execute(
        select(SlugFetch)
        .where(
            SlugFetch.queued_at.is_not(None),
            (SlugFetch.claimed_at.is_(None)) | (SlugFetch.claimed_at < cutoff),
            SlugFetch.is_invalid.is_(False),
        )
        .order_by(SlugFetch.queued_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    now = datetime.now(UTC)
    for row in rows:
        row.claimed_at = now
    if rows:
        await session.commit()
    return rows


async def pending_count(session: AsyncSession) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(SlugFetch)
        .where(
            SlugFetch.queued_at.is_not(None),
            SlugFetch.is_invalid.is_(False),
        )
    )
    return int(result.scalar_one())
