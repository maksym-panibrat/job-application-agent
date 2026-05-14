"""Deterministic guard for recurring office attendance requirements."""

import re
from dataclasses import dataclass
from typing import Protocol


class ProfileLike(Protocol):
    target_locations: list[str]
    remote_ok: bool


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
REMOTE_ONLY_GAP = "remote-only profile but job is not explicitly remote"


def evaluate_remote_policy(profile: ProfileLike, job: JobLike) -> RemotePolicyVerdict:
    text = _job_text(job)

    if not _requires_office_attendance(text):
        if _is_remote_only_profile(profile) and not _is_explicitly_remote(job):
            return RemotePolicyVerdict(hard_mismatch=True, gap=REMOTE_ONLY_GAP)
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
    target_locations = _real_target_locations(profile)
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


def _is_remote_only_profile(profile: ProfileLike) -> bool:
    return bool(getattr(profile, "remote_ok", False)) and not _real_target_locations(profile)


def _real_target_locations(profile: ProfileLike) -> list[str]:
    target_locations = getattr(profile, "target_locations", None) or []
    return [
        str(location)
        for location in target_locations
        if location and not _is_remote_pseudo_location(str(location))
    ]


def _is_remote_pseudo_location(location: str) -> bool:
    normalized = _normalize_text(location).strip()
    return normalized == "remote" or normalized.startswith("remote ")


def _is_explicitly_remote(job: JobLike) -> bool:
    location = _normalize_text(str(getattr(job, "location", None) or ""))
    workplace_type = _normalize_text(str(getattr(job, "workplace_type", None) or ""))
    description = _normalize_text(_job_description_text(job))

    if _contains_token_phrase(workplace_type, "remote"):
        return True
    if _contains_token_phrase(location, "remote"):
        return True
    return _description_has_remote_work_evidence(description)


def _description_has_remote_work_evidence(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    negative_patterns = (
        r"\b(?:not|non|no)\s+remote\b",
        r"\bremote\s+(?:work\s+)?(?:not|unavailable|unsupported)\b",
        r"\bdoes\s+not\s+(?:allow|support|offer)\s+remote\b",
    )
    if any(re.search(pattern, normalized_text) is not None for pattern in negative_patterns):
        return False
    positive_patterns = (
        r"\bremote\s+(?:role|position|job|work|employee|employees|team)\b",
        r"\b(?:fully|100)\s+remote\b",
        r"\bwork\s+from\s+home\b",
        r"\bwfh\b",
    )
    return any(re.search(pattern, normalized_text) is not None for pattern in positive_patterns)
