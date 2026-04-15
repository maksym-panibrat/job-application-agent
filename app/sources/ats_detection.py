"""
ATS URL detection: determine which ATS platform a job posting URL belongs to.
Returns the ATS type and, for Greenhouse, the board token needed for API submission.
"""

import re
from typing import Literal

ATSType = Literal["greenhouse", "lever", "ashby", None]


def detect_ats_type(apply_url: str) -> ATSType:
    """Detect which ATS platform a job URL belongs to."""
    url = apply_url.lower()
    if "greenhouse.io" in url or "boards.greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url or "jobs.lever.co" in url:
        return "lever"
    if "ashby.io" in url or "jobs.ashbyhq.com" in url:
        return "ashby"
    return None


def extract_greenhouse_board_token(apply_url: str) -> str | None:
    """
    Extract the Greenhouse board token from a job posting URL.
    Example: https://boards.greenhouse.io/acmecorp/jobs/123 -> "acmecorp"
    """
    match = re.search(r"boards\.greenhouse\.io/([^/\?#]+)", apply_url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def supports_api_apply(apply_url: str) -> bool:
    """Return True only when API submission is possible (Greenhouse with board token)."""
    if detect_ats_type(apply_url) != "greenhouse":
        return False
    return extract_greenhouse_board_token(apply_url) is not None
