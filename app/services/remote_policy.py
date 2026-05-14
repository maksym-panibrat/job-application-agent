"""Deterministic guard for recurring office attendance requirements."""

from dataclasses import dataclass
import re
from typing import Protocol


class ProfileLike(Protocol):
    target_locations: list[str]


class JobLike(Protocol):
    location: str | None
    workplace_type: str | None
    description: str | None
    description_raw: str | None


@dataclass(frozen=True)
class RemotePolicyVerdict:
    hard_mismatch: bool
    gap: str | None = None


OFFICE_ATTENDANCE_PATTERNS = (
    r"\b(?:requires?|required|must)\b.{0,80}\b(?:office|onsite|on site)\b",
    r"\b(?:office|onsite|on site)\b.{0,80}\b(?:requires?|required|must|minimum)\b",
    r"\b(?:minimum\s+)?\d+\s+days?\s+(?:per\s+)?week\b.{0,80}\b(?:office|onsite|on site)\b",
    r"\b(?:office|onsite|on site)\b.{0,80}\b(?:minimum\s+)?\d+\s+days?\s+(?:per\s+)?week\b",
    r"\bwork\s+from\b.{0,80}\b(?:office|onsite|on site)\b",
    r"\b(?:office|onsite|on site)\b.{0,80}\btwice\s+a\s+week\b",
    r"\btwice\s+a\s+week\b.{0,80}\b(?:office|onsite|on site)\b",
    r"\bhybrid\s+schedule\b.{0,40}\b(?:requires?|required|must)\b",
    r"\b(?:requires?|required|must)\b.{0,40}\bhybrid\s+schedule\b",
    r"\bmust(?:\s+be)?\s+located\s+near\b",
)
OFFICE_ATTENDANCE_GAP = "Requires recurring office attendance outside target locations"


def evaluate_remote_policy(profile: ProfileLike, job: JobLike) -> RemotePolicyVerdict:
    text = _job_text(job)

    if not _requires_office_attendance(text):
        return RemotePolicyVerdict(hard_mismatch=False)

    if _matches_target_location(profile, _job_description_text(job)):
        return RemotePolicyVerdict(hard_mismatch=False)

    return RemotePolicyVerdict(hard_mismatch=True, gap=OFFICE_ATTENDANCE_GAP)


def _job_text(job: JobLike) -> str:
    fields = (
        getattr(job, "location", None),
        getattr(job, "workplace_type", None),
        getattr(job, "description", None),
        getattr(job, "description_raw", None),
    )
    return " ".join(str(field) for field in fields if field).lower()


def _job_description_text(job: JobLike) -> str:
    fields = (
        getattr(job, "description", None),
        getattr(job, "description_raw", None),
    )
    return " ".join(str(field) for field in fields if field).lower()


def _requires_office_attendance(text: str) -> bool:
    normalized_text = _normalize_text(text)
    return any(
        re.search(pattern, normalized_text) is not None
        for pattern in OFFICE_ATTENDANCE_PATTERNS
    )


def _matches_target_location(profile: ProfileLike, text: str) -> bool:
    target_locations = getattr(profile, "target_locations", None) or []
    normalized_text = _normalize_text(text)
    return any(
        _contains_token_phrase(normalized_text, str(location))
        for location in target_locations
        if location
    )


def _contains_token_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text(phrase).strip()
    if not normalized_phrase:
        return False

    pattern = rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower())
