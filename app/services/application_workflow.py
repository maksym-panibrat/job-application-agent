"""Single-writer helpers for application lifecycle transitions."""

from datetime import UTC, datetime

from app.contracts.workflow import (
    GENERATION_REQUESTABLE_STATUSES,
    ApplicationStatus,
    GenerationStatus,
)
from app.models.application import Application


class IllegalApplicationTransition(Exception):
    """Raised when an application lifecycle transition is not allowed."""


def _now() -> datetime:
    return datetime.now(UTC)


def mark_scored(
    application: Application,
    *,
    score: float,
    threshold: float,
    summary: str | None = None,
    rationale: str | None = None,
    strengths: list[str] | None = None,
    gaps: list[str] | None = None,
    now: datetime | None = None,
) -> None:
    application.match_score = score
    if summary is not None:
        application.match_summary = summary
    if rationale is not None:
        application.match_rationale = rationale
    if strengths is not None:
        application.match_strengths = strengths
    if gaps is not None:
        application.match_gaps = gaps
    application.status = (
        ApplicationStatus.PENDING_REVIEW
        if score >= threshold
        else ApplicationStatus.AUTO_REJECTED
    )
    application.updated_at = now or _now()


def mark_user_decision(
    application: Application,
    status: ApplicationStatus | str,
    *,
    now: datetime | None = None,
) -> None:
    status = ApplicationStatus(status)
    if status not in {
        ApplicationStatus.PENDING_REVIEW,
        ApplicationStatus.DISMISSED,
        ApplicationStatus.APPLIED,
    }:
        raise IllegalApplicationTransition(f"cannot set user decision to {status}")
    timestamp = now or _now()
    application.status = status
    application.applied_at = timestamp if status == ApplicationStatus.APPLIED else None
    application.updated_at = timestamp


def request_generation(
    application: Application,
    *,
    now: datetime | None = None,
) -> GenerationStatus:
    current = GenerationStatus(application.generation_status)
    if current not in GENERATION_REQUESTABLE_STATUSES:
        raise IllegalApplicationTransition(f"cannot request generation from {current}")
    application.generation_status = GenerationStatus.PENDING
    application.updated_at = now or _now()
    return GenerationStatus.PENDING


def mark_generation_generating(
    application: Application,
    *,
    now: datetime | None = None,
) -> None:
    application.generation_status = GenerationStatus.GENERATING
    application.generation_attempts += 1
    application.updated_at = now or _now()


def mark_generation_ready(
    application: Application,
    *,
    content: str,
    now: datetime | None = None,
) -> None:
    timestamp = now or _now()
    application.generation_status = GenerationStatus.READY
    application.cover_letter_content = content
    application.generated_at = timestamp
    application.updated_at = timestamp


def mark_generation_failed(
    application: Application,
    *,
    now: datetime | None = None,
) -> None:
    application.generation_status = GenerationStatus.FAILED
    application.updated_at = now or _now()
