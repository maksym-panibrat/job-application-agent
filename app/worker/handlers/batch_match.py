"""batch-match handler: advance one profile's LLM batch matching tick."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.work_queue import WorkQueue
from app.services.batch_match_provider import get_batch_match_provider
from app.services.batch_match_service import run_batch_match_tick
from app.worker.handlers import HANDLERS, EnqueueAfterDone
from app.worker.payloads import BatchMatchPayload

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
        provider = get_batch_match_provider()
        result = await run_batch_match_tick(
            session,
            profile_id=payload.profile_id,
            provider=provider,
            max_items=payload.max_items,
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
        if result.requeued or result.submitted:
            settings = get_settings()
            return EnqueueAfterDone(
                job_type="batch-match",
                payload={
                    key: value
                    for key, value in {
                        "profile_id": str(payload.profile_id),
                        "max_items": payload.max_items,
                    }.items()
                    if value is not None
                },
                dedupe_key=f"batch-match:{payload.profile_id}",
                not_before_seconds=settings.batch_match_poll_interval_seconds,
            )
        return None


HANDLERS["batch-match"] = BatchMatchHandler()
