"""fetch-slug handler: drain one provider slug and enqueue follow-up matching."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.workflow import JobType
from app.database import get_session_factory
from app.models.work_queue import WorkQueue
from app.sources.base import TransientFetchError
from app.worker.handlers import HANDLERS, TransientError
from app.worker.payloads import FetchSlugPayload

log = structlog.get_logger()


class FetchSlugHandler:
    max_attempts = 5

    async def on_terminal_failure(self, session_factory, row: WorkQueue, error: str) -> None:
        del session_factory
        payload = FetchSlugPayload(**row.payload)
        await log.awarning(
            "worker.fetch_slug.terminal_failure",
            provider=payload.provider,
            slug=payload.slug,
            error=error,
        )

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> None:
        del session
        payload = FetchSlugPayload(**row.payload)
        from app.scheduler.tasks import fetch_one_slug

        try:
            counts = await fetch_one_slug(
                provider=payload.provider,
                slug=payload.slug,
                batch_match_max_items=payload.batch_match_max_items,
                session_factory=get_session_factory(),
            )
        except TransientFetchError as exc:
            raise TransientError(str(exc)) from exc

        await log.ainfo(
            "worker.fetch_slug.done",
            provider=payload.provider,
            slug=payload.slug,
            **counts,
        )


HANDLERS[JobType.FETCH_SLUG] = FetchSlugHandler()
