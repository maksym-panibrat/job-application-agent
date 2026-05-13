"""match handler: score one application."""
import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlmodel import select

from app.models.application import Application
from app.models.work_queue import WorkQueue
from app.worker.handlers import HANDLERS, TransientError
from app.worker.payloads import MatchPayload

log = structlog.get_logger()


class MatchHandler:
    max_attempts = 5

    async def on_terminal_failure(self, session_factory, row: WorkQueue, error: str) -> None:
        payload = MatchPayload(**row.payload)
        async with session_factory() as session:
            await session.execute(
                update(Application)
                .where(
                    Application.id == payload.application_id,
                    Application.match_status == "pending_match",
                )
                .values(match_status="match_failed", updated_at=func.now())
            )
            await session.commit()
        await log.awarning(
            "worker.match.terminal_failure",
            application_id=str(payload.application_id),
            error=error,
        )

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> None:
        payload = MatchPayload(**row.payload)
        app = (
            await session.execute(
                select(Application).where(Application.id == payload.application_id)
            )
        ).scalar_one_or_none()
        if app is None:
            await log.awarning(
                "worker.match.application_missing",
                application_id=str(payload.application_id),
            )
            return
        if app.match_score is not None and app.match_status == "matched":
            await log.ainfo("worker.match.skip_replay", application_id=str(app.id))
            return

        from app.agents import matching_agent

        try:
            result = await matching_agent.score_one(application=app, session=session)
        except Exception as exc:
            from httpx import HTTPStatusError

            if isinstance(exc, HTTPStatusError) and exc.response.status_code == 429:
                retry_after_header = exc.response.headers.get("Retry-After")
                retry_after = (
                    int(retry_after_header)
                    if retry_after_header and retry_after_header.isdigit()
                    else None
                )
                raise TransientError(str(exc), retry_after_seconds=retry_after) from exc
            raise

        app.match_score = result["score"]
        app.match_summary = result["summary"]
        app.match_rationale = result.get("rationale")
        app.match_strengths = result.get("strengths", [])
        app.match_gaps = result.get("gaps", [])
        app.match_status = "matched"
        app.match_queued_at = None
        app.match_claimed_at = None
        session.add(app)
        await log.ainfo("worker.match.done", application_id=str(app.id), score=result["score"])


HANDLERS["match"] = MatchHandler()
