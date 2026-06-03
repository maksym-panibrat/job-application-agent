"""Stable application workflow and worker queue wire contracts."""

from enum import StrEnum
from uuid import UUID


class ApplicationStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    AUTO_REJECTED = "auto_rejected"
    DISMISSED = "dismissed"
    APPLIED = "applied"


REVIEWABLE_APPLICATION_STATUSES = (
    ApplicationStatus.PENDING_REVIEW,
    ApplicationStatus.AUTO_REJECTED,
)
USER_DECISION_STATUSES = (
    ApplicationStatus.DISMISSED,
    ApplicationStatus.APPLIED,
)


class GenerationStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


GENERATION_REQUESTABLE_STATUSES = (
    GenerationStatus.NONE,
    GenerationStatus.READY,
    GenerationStatus.FAILED,
)
GENERATION_IN_FLIGHT_STATUSES = (
    GenerationStatus.PENDING,
    GenerationStatus.GENERATING,
)


class JobType(StrEnum):
    FETCH_SLUG = "fetch-slug"
    MATCH = "match"
    BATCH_MATCH = "batch-match"
    GENERATE_COVER_LETTER = "generate-cover-letter"
    MAINTENANCE = "maintenance"


def _as_id(value: UUID | str) -> str:
    return str(value)


def _safe_key_part(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must not be empty")
    if ":" in value:
        raise ValueError(f"{label} must not contain ':'")
    return value


def fetch_slug_dedupe_key(provider: str, slug: str) -> str:
    provider = _safe_key_part(provider, label="provider")
    slug = _safe_key_part(slug, label="slug")
    return f"{JobType.FETCH_SLUG}:{provider}:{slug}"


def match_dedupe_key(application_id: UUID | str) -> str:
    return f"{JobType.MATCH}:{_as_id(application_id)}"


def batch_match_dedupe_key(profile_id: UUID | str) -> str:
    return f"{JobType.BATCH_MATCH}:{_as_id(profile_id)}"


def cover_letter_dedupe_key(application_id: UUID | str) -> str:
    return f"{JobType.GENERATE_COVER_LETTER}:{_as_id(application_id)}"
