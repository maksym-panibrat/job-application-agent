"""Repair durable external batch state into executable worker jobs."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.llm_match_batch import ACTIVE_BATCH_STATUSES, LLMMatchBatch
from app.worker.enqueue_intents import enqueue_batch_match


async def enqueue_due_batch_polls(
    session: AsyncSession,
    *,
    profile_id: UUID | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Enqueue batch-match ticks for active provider batches due to be polled.

    ``LLMMatchBatch.next_poll_at`` is the durable scheduling contract for
    external provider batches. Work-queue rows are execution attempts and may be
    lost/pruned/deduped; this reconciler repairs those rows from batch state.
    """
    now = now or datetime.now(UTC)
    query = select(LLMMatchBatch).where(
        col(LLMMatchBatch.status).in_(ACTIVE_BATCH_STATUSES),
        col(LLMMatchBatch.next_poll_at).is_not(None),
        col(LLMMatchBatch.next_poll_at) <= now,
    )
    if profile_id is not None:
        query = query.where(LLMMatchBatch.profile_id == profile_id)

    batches = (await session.execute(query)).scalars().all()
    enqueued: list[int] = []
    seen_profiles: set[UUID] = set()
    for batch in batches:
        if batch.profile_id in seen_profiles:
            continue
        seen_profiles.add(batch.profile_id)
        row_id = await enqueue_batch_match(session, batch.profile_id)
        if row_id is not None:
            enqueued.append(row_id)
    return enqueued
