"""Company resolution service.

Algorithm:
  1. Normalize input (lowercase, strip, hyphenate whitespace).
  2. Cache lookup by normalized_key.
  3. On miss: parallel validate() across all SOURCES with a wall timeout.
  4. Persist confirmed providers via ON CONFLICT (normalized_key) DO NOTHING
     RETURNING; on no-row-returned, re-SELECT (concurrent-resolve race).
  5. Return Company or None.
"""

import asyncio
import re
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.sources import SOURCES

DEFAULT_FANOUT_TIMEOUT = 3.0  # seconds

log = structlog.get_logger()

ProbeResult = bool | Literal["error"]


class FanoutTimeoutError(Exception):
    """Raised when validate() across all SOURCES exceeds the wall timeout.

    Distinguishes from 'no match' (resolve returns None): the API layer
    converts this to 503 so the user can retry, vs 404 for confirmed miss.
    """


def _normalize(text: str) -> str:
    """Trim, lowercase, collapse internal whitespace runs to single hyphens."""
    s = text.strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s


async def _fan_out(slug: str, *, timeout: float) -> dict[str, ProbeResult]:
    """Run validate() across every adapter in parallel with a shared wall timeout.

    Returns a dict mapping provider_name -> True (200), False (404), or
    'error' (5xx, network, malformed). Raises asyncio.TimeoutError if the
    aggregate wall exceeds `timeout`.
    """

    async def probe(provider: str, src):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                ok = await src.validate(slug, client=client)
            return provider, bool(ok)
        except Exception:
            return provider, "error"

    coros = [probe(p, s) for p, s in SOURCES.items()]
    results = await asyncio.wait_for(asyncio.gather(*coros), timeout=timeout)
    return {p: r for p, r in results}


async def resolve(input_text: str, session: AsyncSession) -> Company | None:
    """Resolve a free-text company input to a Company row, fan-out + cache.

    Returns None on no-match. Raises FanoutTimeoutError on fan-out timeout.
    """
    normalized = _normalize(input_text)
    if not normalized:
        return None

    # Cache lookup
    existing = (
        await session.execute(select(Company).where(Company.normalized_key == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        await log.adebug("company_resolver.cache_hit", normalized=normalized)
        return existing

    # Fan out
    try:
        results = await _fan_out(normalized, timeout=DEFAULT_FANOUT_TIMEOUT)
    except TimeoutError as exc:
        await log.awarning("company_resolver.fanout_timeout", normalized=normalized)
        raise FanoutTimeoutError(normalized) from exc

    confirmed = {p: normalized for p, r in results.items() if r is True}
    failed = [p for p, r in results.items() if r == "error"]

    if not confirmed:
        await log.ainfo("company_resolver.no_match", normalized=normalized, failed=failed)
        return None

    if failed:
        await log.awarning(
            "company_resolver.partial_match",
            normalized=normalized,
            confirmed=list(confirmed),
            failed=failed,
        )

    canonical = " ".join(w.capitalize() for w in normalized.split("-"))
    now = datetime.now(UTC)
    stmt = (
        insert(Company)
        .values(
            canonical_name=canonical,
            normalized_key=normalized,
            provider_slugs=confirmed,
            resolved_at=now,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["normalized_key"])
        .returning(Company.id)
    )
    result = await session.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    await session.commit()

    if inserted_id is None:
        existing = (
            await session.execute(select(Company).where(Company.normalized_key == normalized))
        ).scalar_one()
        await log.ainfo("company_resolver.match_concurrent", normalized=normalized)
        return existing

    company = (await session.execute(select(Company).where(Company.id == inserted_id))).scalar_one()
    await log.ainfo(
        "company_resolver.match",
        normalized=normalized,
        providers=list(confirmed),
    )
    return company
