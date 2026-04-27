"""
Greenhouse ATS enricher and applicant-side API submitter.

Board token is extracted from the job URL (public, no employer key needed).
API: https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}
"""

import re

import structlog
from httpx import AsyncClient

log = structlog.get_logger()


def extract_greenhouse_board_token(apply_url: str) -> str | None:
    """Extract the Greenhouse board token from a job posting URL.

    Example: https://boards.greenhouse.io/acmecorp/jobs/123 -> "acmecorp"
    """
    match = re.search(r"boards\.greenhouse\.io/([^/\?#]+)", apply_url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseUnavailable(Exception):
    """Greenhouse boards API returned an error or was unreachable."""


async def get_job_questions(board_token: str, job_id: str) -> list[dict]:
    """
    Fetch custom questions for a Greenhouse job posting.
    Returns list of dicts with keys: label, type, required.

    Raises GreenhouseUnavailable on HTTP non-200 responses or network errors.
    Callers can distinguish this from the "no custom questions" case
    (an empty list from a 200 response with no questions).
    """
    url = f"{BOARDS_API}/{board_token}/jobs/{job_id}"
    try:
        async with AsyncClient(timeout=15) as client:
            resp = await client.get(url, params={"questions": "true"})
            if resp.status_code != 200:
                raise GreenhouseUnavailable(f"HTTP {resp.status_code}")
            data = resp.json()
    except GreenhouseUnavailable:
        raise
    except Exception as exc:
        await log.aerror(
            "greenhouse.questions_failed",
            source_name="greenhouse",
            board_token=board_token,
            job_id=job_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise GreenhouseUnavailable(str(exc)) from exc

    questions = []
    for q in data.get("questions", []):
        label = q.get("label", "")
        if label and q.get("type") not in ("attachment", "input_file"):
            questions.append(
                {
                    "label": label,
                    "type": q.get("type", "input_text"),
                    "required": bool(q.get("required", False)),
                }
            )
    return questions


async def get_job_questions_by_url(apply_url: str) -> list[dict]:
    """
    Convenience wrapper: extract board token and job ID from a Greenhouse apply URL,
    then delegate to get_job_questions.

    Returns [] when the URL is not a parseable Greenhouse URL (missing board
    token or job id) — those are parse failures, not availability issues.
    Propagates GreenhouseUnavailable from the underlying network call.
    """
    import re

    board_token = extract_greenhouse_board_token(apply_url)
    if not board_token:
        return []
    job_match = re.search(r"/jobs/(\d+)", apply_url)
    if not job_match:
        return []
    return await get_job_questions(board_token, job_match.group(1))


async def submit_application(
    board_token: str,
    job_id: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str | None,
    resume_md: str | None,
    cover_letter_md: str | None,
    custom_answers: dict[str, str] | None = None,
) -> dict:
    """
    Submit a job application via Greenhouse applicant-side API.
    Returns {"success": True} or {"success": False, "error": "..."}.

    Note: resume/cover letter are sent as plaintext (simplest upload method).
    For binary uploads (PDF), use the base64 method instead.
    """
    url = f"{BOARDS_API}/{board_token}/jobs/{job_id}"

    payload: dict = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
    }
    if phone:
        payload["phone"] = phone
    if resume_md:
        payload["resume_text"] = resume_md
    if cover_letter_md:
        payload["cover_letter_text"] = cover_letter_md

    # Map custom answers to question IDs (if provided as {question_label: answer})
    if custom_answers:
        answers = []
        for question, answer in custom_answers.items():
            answers.append({"question": question, "answer": answer})
        payload["answers"] = answers

    async with AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code in (200, 201):
        return {"success": True, "status_code": resp.status_code}
    return {
        "success": False,
        "status_code": resp.status_code,
        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
    }


async def try_submit(
    apply_url: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str | None = None,
    resume_md: str | None = None,
    cover_letter_md: str | None = None,
    custom_answers: dict[str, str] | None = None,
) -> dict:
    """
    High-level submit: extract board token from URL, submit, return result.
    Returns {"success": True, "method": "api"} or {"success": False, "method": "manual"}.
    """
    board_token = extract_greenhouse_board_token(apply_url)
    if not board_token:
        return {"success": False, "method": "manual", "error": "No board token in URL"}

    # Extract job_id from URL: .../jobs/123456 -> "123456"
    import re

    job_match = re.search(r"/jobs/(\d+)", apply_url)
    if not job_match:
        return {"success": False, "method": "manual", "error": "No job ID in URL"}

    job_id = job_match.group(1)

    try:
        result = await submit_application(
            board_token=board_token,
            job_id=job_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            resume_md=resume_md,
            cover_letter_md=cover_letter_md,
            custom_answers=custom_answers,
        )
    except Exception as exc:
        await log.aerror(
            "greenhouse.submit_failed",
            source_name="greenhouse",
            board_token=board_token,
            job_id=job_id,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "success": False,
            "method": "greenhouse_api",
            "status_code": None,
            "error": str(exc),
        }
    result["method"] = "greenhouse_api"
    return result
