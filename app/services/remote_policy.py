"""Deterministic guard for recurring office attendance requirements."""

import re
from dataclasses import dataclass
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
NON_US_POSITION_GAP = "Position is not US-based"

US_STATE_NAMES = (
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
)
US_STATE_ABBREVIATIONS = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
)
US_STATE_ABBREVIATION_PATTERN = "|".join(US_STATE_ABBREVIATIONS)
US_COUNTRY_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])united\s+states(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])u\.s\.a?\.?(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])usa(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])US(?![A-Za-z0-9])"),
)
NON_US_LOCATION_TOKENS = (
    "canada",
    "united kingdom",
    "uk",
    "germany",
    "tbilisi",
    "india",
    "europe",
    "european union",
)
CITY_STATE_RE = re.compile(
    rf"(?<!,\s)\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){{0,3}},\s*"
    rf"(?:{US_STATE_ABBREVIATION_PATTERN})(?![A-Za-z0-9])"
)
CONTEXTUAL_STATE_ABBREVIATION_RE = re.compile(
    rf"\b(?:based\s+in|located\s+in|from|in|within|near)\s+"
    rf"(?:{US_STATE_ABBREVIATION_PATTERN})(?![A-Za-z0-9])"
)
US_COUNTRY_OR_STATE_NAME_PATTERN = "|".join(
    (
        r"united\s+states",
        r"u\.s\.a?\.?",
        "usa",
        *tuple(re.escape(state) for state in US_STATE_NAMES),
    )
)
US_LOCATION_NAME_TOKEN_PATTERN = rf"(?:the\s+)?(?:{US_COUNTRY_OR_STATE_NAME_PATTERN})"
US_LOCATION_ABBREVIATION_TOKEN_PATTERN = rf"(?:US|{US_STATE_ABBREVIATION_PATTERN})"
EXCLUSIONARY_US_PREFIX_PATTERN = (
    r"(?:not\s+(?:available|open|accepted|considered|allowed|permitted)"
    r"|unavailable|excluding|excludes?|except(?:ing)?|outside(?:\s+of)?|not\s+based)"
)
EXCLUSIONARY_US_SUFFIX_PATTERN = (
    r"(?:not\s+(?:eligible|accepted|considered|allowed|permitted|available)"
    r"|ineligible|excluded|unavailable)"
)
EXCLUSIONARY_US_NAME_PATTERNS = (
    re.compile(
        rf"\b{EXCLUSIONARY_US_PREFIX_PATTERN}\b.{{0,80}}"
        rf"(?<![A-Za-z0-9]){US_LOCATION_NAME_TOKEN_PATTERN}(?![A-Za-z0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<![A-Za-z0-9]){US_LOCATION_NAME_TOKEN_PATTERN}(?![A-Za-z0-9])"
        rf".{{0,80}}\b(?:applicants?|candidates?|residents?|workers?)?\s*"
        rf"(?:are|is)?\s*{EXCLUSIONARY_US_SUFFIX_PATTERN}\b",
        re.IGNORECASE,
    ),
)
EXCLUSIONARY_US_ABBREVIATION_PATTERNS = (
    re.compile(
        rf"\b(?i:{EXCLUSIONARY_US_PREFIX_PATTERN})\b.{{0,80}}"
        rf"(?<![A-Za-z0-9-]){US_LOCATION_ABBREVIATION_TOKEN_PATTERN}"
        rf"(?![A-Za-z0-9])"
    ),
    re.compile(
        rf"(?<![A-Za-z0-9-]){US_LOCATION_ABBREVIATION_TOKEN_PATTERN}"
        rf"(?![A-Za-z0-9]).{{0,80}}"
        rf"\b(?i:(?:applicants?|candidates?|residents?|workers?)?\s*"
        rf"(?:are|is)?\s*{EXCLUSIONARY_US_SUFFIX_PATTERN})\b"
    ),
)


def evaluate_remote_policy(profile: ProfileLike, job: JobLike) -> RemotePolicyVerdict:
    text = _job_text(job)

    if not _requires_office_attendance(text):
        return RemotePolicyVerdict(hard_mismatch=False)

    if _matches_target_location(profile, _job_description_text(job)):
        return RemotePolicyVerdict(hard_mismatch=False)

    return RemotePolicyVerdict(hard_mismatch=True, gap=OFFICE_ATTENDANCE_GAP)


def evaluate_us_location_policy(job: JobLike) -> RemotePolicyVerdict:
    if _has_overriding_non_us_location_signal(job):
        return RemotePolicyVerdict(hard_mismatch=True, gap=NON_US_POSITION_GAP)

    if _has_us_location_signal(job):
        return RemotePolicyVerdict(hard_mismatch=False)

    return RemotePolicyVerdict(hard_mismatch=True, gap=NON_US_POSITION_GAP)


def _job_text(job: JobLike) -> str:
    return _job_owned_text(job).lower()


def _job_owned_text(job: JobLike) -> str:
    fields = (
        getattr(job, "location", None),
        getattr(job, "workplace_type", None),
        getattr(job, "description", None),
        getattr(job, "description_raw", None),
    )
    return " ".join(str(field) for field in fields if field)


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


def _has_us_location_signal(job: JobLike) -> bool:
    text = _job_owned_text(job)
    normalized_text = _normalize_text(text)

    if _has_exclusionary_us_location_signal(text):
        return False

    return (
        any(pattern.search(text) is not None for pattern in US_COUNTRY_PATTERNS)
        or any(_contains_token_phrase(normalized_text, state) for state in US_STATE_NAMES)
        or CITY_STATE_RE.search(text) is not None
        or CONTEXTUAL_STATE_ABBREVIATION_RE.search(text) is not None
    )


def _has_overriding_non_us_location_signal(job: JobLike) -> bool:
    location = getattr(job, "location", None)
    if not location:
        return False

    location_text = str(location)
    if any(pattern.search(location_text) for pattern in US_COUNTRY_PATTERNS):
        return False

    normalized_location = _normalize_text(location_text)
    return any(
        _contains_token_phrase(normalized_location, token)
        for token in NON_US_LOCATION_TOKENS
    )


def _has_exclusionary_us_location_signal(text: str) -> bool:
    return any(
        pattern.search(text) is not None
        for pattern in (
            *EXCLUSIONARY_US_NAME_PATTERNS,
            *EXCLUSIONARY_US_ABBREVIATION_PATTERNS,
        )
    )


def _contains_token_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text(phrase).strip()
    if not normalized_phrase:
        return False

    pattern = rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower())
