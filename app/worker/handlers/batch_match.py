"""batch-match handler: advance one profile's LLM batch matching tick."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work_queue import WorkQueue
from app.services.batch_match_provider import FakeBatchMatchProvider
from app.services.batch_match_service import run_batch_match_tick
from app.worker.handlers import HANDLERS
from app.worker.payloads import BatchMatchPayload

log = structlog.get_logger()


class BatchMatchHandler:
    max_attempts = 5

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> None:
        payload = BatchMatchPayload(**row.payload)
        provider = FakeBatchMatchProvider(ready=False)
        result = await run_batch_match_tick(
            session,
            profile_id=payload.profile_id,
            provider=provider,
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


HANDLERS["batch-match"] = BatchMatchHandler()
