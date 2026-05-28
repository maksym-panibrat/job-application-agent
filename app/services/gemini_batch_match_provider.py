from __future__ import annotations

import json

from app.agents.matching_agent import SCORING_SYSTEM_PROMPT
from app.services.batch_match_provider import ProviderBatchOutput, ProviderBatchStatus


def build_gemini_batch_request(request_key: str, profile_text: str, jobs: list[dict]) -> dict:
    prompt = _build_prompt(profile_text=profile_text, jobs=jobs)
    return {
        "key": request_key,
        "request": {"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
    }


class GeminiBatchMatchProvider:
    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        raise RuntimeError("Gemini batch submit API wiring is required before use")

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        raise RuntimeError("Gemini batch poll API wiring is required before use")

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        raise RuntimeError("Gemini batch output API wiring is required before use")


def _build_prompt(*, profile_text: str, jobs: list[dict]) -> str:
    jobs_json = json.dumps(jobs, ensure_ascii=True, indent=2, sort_keys=True)
    application_ids = [str(job.get("application_id", "")) for job in jobs]
    application_ids_json = json.dumps(application_ids, ensure_ascii=True)

    return (
        f"""\
{SCORING_SYSTEM_PROMPT}

Score the candidate profile against every job below.

Return only a top-level JSON object with a "results" array. Include exactly one """
        f"""result per application_id from this list:
{application_ids_json}.

Each result must include:
- application_id
- score
- summary
- rationale
- strengths
- gaps

PROFILE:
{profile_text}

JOBS:
{jobs_json}
"""
    )
