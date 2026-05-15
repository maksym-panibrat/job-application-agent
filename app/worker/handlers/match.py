"""match handler: score one application."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue
from app.services.remote_policy import evaluate_remote_policy, evaluate_us_location_policy
from app.worker.handlers import HANDLERS, TransientError
from app.worker.payloads import MatchPayload

log = structlog.get_logger()


def _deterministic_rejection_score(threshold: float) -> float:
    return max(0.0, min(0.29, threshold - 0.01))


class MatchHandler:
    max_attempts = 5

    async def on_terminal_failure(self, session_factory, row: WorkQueue, error: str) -> None:
        del session_factory
        payload = MatchPayload(**row.payload)
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
        if app.match_score is not None:
            await log.ainfo("worker.match.skip_replay", application_id=str(app.id))
            return

        job = await session.get(Job, app.job_id)
        profile = await session.get(UserProfile, app.profile_id)
        if job is None or profile is None:
            await log.awarning(
                "worker.match.domain_missing",
                application_id=str(app.id),
                job_id=str(app.job_id),
                profile_id=str(app.profile_id),
                job_found=job is not None,
                profile_found=profile is not None,
            )
            return

        verdict = evaluate_us_location_policy(job)
        if verdict.hard_mismatch:
            settings = get_settings()
            gap = verdict.gap or "Deterministic match policy mismatch"
            app.match_score = _deterministic_rejection_score(
                settings.match_score_threshold
            )
            app.match_summary = "Deterministic mismatch: non-US position"
            app.match_rationale = gap
            app.match_strengths = []
            app.match_gaps = [gap]
            if app.status == "pending_review":
                app.status = "auto_rejected"
            session.add(app)
            await log.ainfo(
                "worker.match.deterministic_reject",
                application_id=str(app.id),
                policy="us_location",
                score=app.match_score,
            )
            return

        verdict = evaluate_remote_policy(profile, job)
        if verdict.hard_mismatch:
            settings = get_settings()
            gap = verdict.gap or "Deterministic match policy mismatch"
            app.match_score = _deterministic_rejection_score(
                settings.match_score_threshold
            )
            app.match_summary = (
                "Deterministic mismatch: recurring office attendance requirement"
            )
            app.match_rationale = gap
            app.match_strengths = []
            app.match_gaps = [gap]
            if app.status == "pending_review":
                app.status = "auto_rejected"
            session.add(app)
            await log.ainfo(
                "worker.match.deterministic_reject",
                application_id=str(app.id),
                policy="remote_office",
                score=app.match_score,
            )
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

        score = result["score"]
        if score is None:
            await log.awarning(
                "worker.match.scoring_skipped",
                application_id=str(app.id),
                rationale=str(result.get("rationale") or "")[:200],
            )
            raise TransientError("matching score skipped")

        app.match_score = score
        app.match_summary = result["summary"]
        app.match_rationale = result.get("rationale")
        app.match_strengths = result.get("strengths", [])
        app.match_gaps = result.get("gaps", [])
        settings = get_settings()
        if score < settings.match_score_threshold and app.status == "pending_review":
            app.status = "auto_rejected"
        session.add(app)
        await log.ainfo("worker.match.done", application_id=str(app.id), score=score)


HANDLERS["match"] = MatchHandler()
