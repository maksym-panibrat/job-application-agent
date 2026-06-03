"""Typed helpers for enqueueing known workflow jobs.

Callers express workflow intent here instead of rebuilding job_type, payload,
and dedupe-key strings throughout the app.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.workflow import (
    JobType,
    batch_match_dedupe_key,
    cover_letter_dedupe_key,
    fetch_slug_dedupe_key,
    match_dedupe_key,
)
from app.worker.queue_service import enqueue


async def enqueue_fetch_slug(
    session: AsyncSession,
    *,
    provider: str,
    slug: str,
    batch_match_max_items: int | None = None,
) -> int | None:
    payload: dict = {"provider": provider, "slug": slug}
    if batch_match_max_items is not None:
        payload["batch_match_max_items"] = batch_match_max_items
    return await enqueue(
        session,
        job_type=JobType.FETCH_SLUG,
        payload=payload,
        dedupe_key=fetch_slug_dedupe_key(provider, slug),
    )


async def enqueue_match(session: AsyncSession, application_id: UUID | str) -> int | None:
    return await enqueue(
        session,
        job_type=JobType.MATCH,
        payload={"application_id": str(application_id)},
        dedupe_key=match_dedupe_key(application_id),
    )


async def enqueue_batch_match(
    session: AsyncSession,
    profile_id: UUID | str,
    *,
    max_items: int | None = None,
    not_before_seconds: int = 0,
) -> int | None:
    payload: dict = {"profile_id": str(profile_id)}
    if max_items is not None:
        payload["max_items"] = max_items
    not_before = None
    if not_before_seconds > 0:
        not_before = datetime.now(UTC) + timedelta(seconds=not_before_seconds)
    return await enqueue(
        session,
        job_type=JobType.BATCH_MATCH,
        payload=payload,
        dedupe_key=batch_match_dedupe_key(profile_id),
        not_before=not_before,
    )


async def enqueue_cover_letter(
    session: AsyncSession, application_id: UUID | str
) -> int | None:
    return await enqueue(
        session,
        job_type=JobType.GENERATE_COVER_LETTER,
        payload={"application_id": str(application_id)},
        dedupe_key=cover_letter_dedupe_key(application_id),
    )
