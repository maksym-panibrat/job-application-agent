"""batch-match handler: advance one profile's LLM batch matching tick."""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts.workflow import JobType, batch_match_dedupe_key
from app.models.work_queue import WorkQueue
from app.services.batch_match_provider import get_batch_match_provider
from app.services.batch_match_service import run_batch_match_tick
from app.worker.handlers import HANDLERS, EnqueueAfterDone
from app.worker.payloads import BatchMatchPayload
from app.worker.queue_service import update_payload

log = structlog.get_logger()


class BatchMatchHandler:
    max_attempts = 5

    async def on_terminal_failure(self, session_factory, row: WorkQueue, error: str) -> None:
        del session_factory
        payload = BatchMatchPayload(**row.payload)
        await log.awarning(
            "worker.batch_match.terminal_failure",
            profile_id=str(payload.profile_id),
            error=error,
        )

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> EnqueueAfterDone | None:
        payload = BatchMatchPayload(**row.payload)
        # Reserve a bounded workflow's entire remaining candidate budget before
        # any domain/provider side effect. A process crash can then only make the
        # canary stop early; replay can never reuse an already-spent budget. On a
        # successful tick we refund the unselected portion below.
        if payload.max_candidates:
            await _checkpoint_budget(session, row, payload, remaining_candidates=0)
            await session.commit()

        provider = get_batch_match_provider()
        result = await run_batch_match_tick(
            session,
            profile_id=payload.profile_id,
            provider=provider,
            max_items=payload.max_items,
            max_candidates=payload.max_candidates,
        )
        await log.ainfo(
            "worker.batch_match.done",
            profile_id=str(payload.profile_id),
            selected=result.selected,
            deterministic_rejected=result.deterministic_rejected,
            submitted=result.submitted,
            imported=result.imported,
            retryable_failed=result.retryable_failed,
            terminal_failed=result.terminal_failed,
            requeued=result.requeued,
        )
        remaining_candidates = payload.max_candidates
        if remaining_candidates is not None:
            remaining_candidates = max(0, remaining_candidates - result.selected)
            await _checkpoint_budget(
                session,
                row,
                payload,
                remaining_candidates=remaining_candidates,
            )
        if result.requeued or result.submitted:
            settings = get_settings()
            return EnqueueAfterDone(
                job_type=JobType.BATCH_MATCH,
                payload=_payload_dict(payload, max_candidates=remaining_candidates),
                dedupe_key=batch_match_dedupe_key(payload.profile_id),
                not_before_seconds=settings.batch_match_poll_interval_seconds,
            )
        return None


def _payload_dict(
    payload: BatchMatchPayload,
    *,
    max_candidates: int | None,
) -> dict[str, str | int]:
    return {
        key: value
        for key, value in {
            "profile_id": str(payload.profile_id),
            "max_items": payload.max_items,
            "max_candidates": max_candidates,
        }.items()
        if value is not None
    }


async def _checkpoint_budget(
    session: AsyncSession,
    row: WorkQueue,
    payload: BatchMatchPayload,
    *,
    remaining_candidates: int,
) -> None:
    if row.id is None or not row.claimed_by:
        raise RuntimeError("bounded batch-match row must have an owned queue lease")
    checkpoint = _payload_dict(payload, max_candidates=remaining_candidates)
    await update_payload(
        session,
        row.id,
        payload=checkpoint,
        worker_id=row.claimed_by,
    )
    # Keep terminal logging/finalization state consistent with the durable row.
    row.payload = checkpoint


HANDLERS[JobType.BATCH_MATCH] = BatchMatchHandler()
