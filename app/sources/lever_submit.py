"""Lever API submit adapter.

Lever supports applicant-side POST to /v0/postings/{site}/{posting-id}?key={api_key}.
Without an employer-published API key, returns {method: "manual"}.
"""

import re

import httpx
import structlog

log = structlog.get_logger()


def _parse_lever_url(apply_url: str) -> tuple[str, str] | None:
    """Extract (site, posting_id) from a Lever URL."""
    m = re.search(r"jobs\.lever\.co/([^/]+)/([a-f0-9-]+)", apply_url)
    return (m.group(1), m.group(2)) if m else None


async def try_submit(
    apply_url: str,
    resume_text: str,
    cover_letter_md: str,
    first_name: str,
    last_name: str,
    email: str,
    api_key: str | None = None,
) -> dict:
    parsed = _parse_lever_url(apply_url)
    if not parsed or not api_key:
        return {"method": "manual", "apply_url": apply_url}

    site, posting_id = parsed
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.lever.co/v0/postings/{site}/{posting_id}",
                params={"key": api_key},
                data={
                    "name": f"{first_name} {last_name}".strip(),
                    "email": email,
                    "resume": resume_text,
                    "cover_letter": cover_letter_md,
                },
            )
            return {
                "method": "lever_api",
                "status_code": resp.status_code,
                "success": resp.status_code in (200, 201),
                "body": resp.text[:500],
            }
    except Exception as exc:
        await log.aerror(
            "lever_submit.failed",
            source_name="lever_submit",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "method": "lever_api",
            "success": False,
            "status_code": None,
            "error": str(exc),
        }
