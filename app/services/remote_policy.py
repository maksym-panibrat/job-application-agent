"""Deterministic guard for recurring office attendance requirements."""

from dataclasses import dataclass
import re
from typing import Protocol


class ProfileLike(Protocol):
    remote_ok: bool
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


OFFICE_TERMS = ("office", "onsite", "on-site", "hybrid")
REQUIREMENT_TERMS = (
    "minimum",
    "required",
    "requires",
    "must",
    "days per week",
    "days/week",
    "work from",
    "located near",
    "twice a week",
)
OFFICE_ATTENDANCE_GAP = "Requires recurring office attendance outside target locations"


def evaluate_remote_policy(profile: ProfileLike, job: JobLike) -> RemotePolicyVerdict:
    text = _job_text(job)

    if not _requires_office_attendance(text):
        return RemotePolicyVerdict(hard_mismatch=False)

    if _matches_target_location(profile, text):
        return RemotePolicyVerdict(hard_mismatch=False)

    if getattr(profile, "remote_ok", False):
        return RemotePolicyVerdict(hard_mismatch=True, gap=OFFICE_ATTENDANCE_GAP)

    return RemotePolicyVerdict(hard_mismatch=True, gap=OFFICE_ATTENDANCE_GAP)


def _job_text(job: JobLike) -> str:
    fields = (
        getattr(job, "location", None),
        getattr(job, "workplace_type", None),
        getattr(job, "description", None),
        getattr(job, "description_raw", None),
    )
    return " ".join(str(field) for field in fields if field).lower()


def _requires_office_attendance(text: str) -> bool:
    has_office_term = any(term in text for term in OFFICE_TERMS)
    has_requirement_language = any(term in text for term in REQUIREMENT_TERMS)
    return has_office_term and has_requirement_language


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
