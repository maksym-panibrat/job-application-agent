from __future__ import annotations

import asyncio
import json
from typing import Any

from google import genai

from app.agents.matching_agent import SCORING_SYSTEM_PROMPT
from app.config import get_settings
from app.services.batch_match_provider import (
    ProviderBatchOutput,
    ProviderBatchStatus,
    ProviderJobResult,
    ProviderRequestResult,
)

_TERMINAL_FAILED_STATES = {
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    "JOB_STATE_PARTIALLY_SUCCEEDED",
}


def build_gemini_batch_request(request_key: str, profile_text: str, jobs: list[dict]) -> dict:
    prompt = _build_prompt(profile_text=profile_text, jobs=jobs)
    return {
        "key": request_key,
        "request": {"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
    }


class GeminiBatchMatchProvider:
    def __init__(self, *, client: Any | None = None, model: str | None = None) -> None:
        if client is not None and model is not None:
            self.client = client
            self.model = model
            return

        settings = get_settings()
        self.model = model or settings.llm_matching_model
        if client is not None:
            self.client = client
            return
        api_key = settings.google_api_key.get_secret_value()
        if not api_key:
            raise ValueError("google_api_key must be set for gemini batch matching")
        self.client = genai.Client(api_key=api_key)

    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        inline_requests = [_inline_request(request) for request in requests]
        batch_job = await asyncio.to_thread(
            self.client.batches.create,
            model=self.model,
            src=inline_requests,
            config={"display_name": display_name},
        )
        provider_batch_id = getattr(batch_job, "name", None)
        if not provider_batch_id:
            raise RuntimeError("Gemini batch create did not return a job name")
        return str(provider_batch_id)

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        batch_job = await asyncio.to_thread(
            self.client.batches.get,
            name=provider_batch_id,
        )
        state = _state_name(getattr(batch_job, "state", None))
        if state == "JOB_STATE_SUCCEEDED":
            return ProviderBatchStatus(ready=True)
        if state in _TERMINAL_FAILED_STATES:
            return ProviderBatchStatus(
                ready=True,
                failed=True,
                error=_error_message(getattr(batch_job, "error", None)) or state,
            )
        return ProviderBatchStatus(ready=False)

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        batch_job = await asyncio.to_thread(
            self.client.batches.get,
            name=provider_batch_id,
        )
        responses = getattr(getattr(batch_job, "dest", None), "inlined_responses", None)
        if responses is None and isinstance(getattr(batch_job, "dest", None), dict):
            responses = batch_job.dest.get("inlined_responses") or batch_job.dest.get(
                "inlinedResponses"
            )
        if responses is None:
            raise RuntimeError("Gemini batch output did not include inline responses")
        return ProviderBatchOutput(
            requests=[_request_result_from_inline_response(response) for response in responses]
        )


def _inline_request(request: dict) -> dict:
    payload = build_gemini_batch_request(
        request_key=str(request["request_key"]),
        profile_text=str(request.get("profile_text") or ""),
        jobs=list(request.get("jobs") or []),
    )
    return {
        **payload["request"],
        "metadata": {"request_key": payload["key"]},
    }


def _request_result_from_inline_response(response: Any) -> ProviderRequestResult:
    request_key = _metadata_request_key(response)
    provider_error = _error_message(_attr_or_key(response, "error"))
    if provider_error:
        return ProviderRequestResult(request_key=request_key, results=[], error=provider_error)

    text = _response_text(_attr_or_key(response, "response"))
    if text is None:
        return ProviderRequestResult(
            request_key=request_key,
            results=[],
            error="provider returned no response text",
        )
    try:
        payload = json.loads(_strip_json_markdown(text))
    except json.JSONDecodeError:
        return ProviderRequestResult(
            request_key=request_key,
            results=[],
            error="provider returned invalid JSON",
        )
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return ProviderRequestResult(
            request_key=request_key,
            results=[],
            error="provider returned JSON without results",
        )
    return ProviderRequestResult(
        request_key=request_key,
        results=[_job_result(raw_result) for raw_result in raw_results],
    )


def _metadata_request_key(response: Any) -> str:
    metadata = _attr_or_key(response, "metadata") or {}
    if isinstance(metadata, dict):
        return str(metadata.get("request_key") or metadata.get("key") or "")
    request_key = getattr(metadata, "request_key", None) or getattr(metadata, "key", None)
    return str(request_key or "")


def _response_text(response: Any) -> str | None:
    parsed = _attr_or_key(response, "parsed")
    if isinstance(parsed, dict):
        return json.dumps(parsed)
    direct_text = _attr_or_key(response, "text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text
    candidates = _attr_or_key(response, "candidates") or []
    for candidate in candidates:
        content = _attr_or_key(candidate, "content")
        for part in _attr_or_key(content, "parts") or []:
            text = _attr_or_key(part, "text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def _strip_json_markdown(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _job_result(raw: Any) -> ProviderJobResult:
    if not isinstance(raw, dict):
        return ProviderJobResult(
            application_id="",
            score=None,
            summary="",
            rationale="",
            strengths=[],
            gaps=[],
            error="provider returned non-object result",
        )
    return ProviderJobResult(
        application_id=str(raw.get("application_id") or ""),
        score=_score(raw.get("score")),
        summary=str(raw.get("summary") or ""),
        rationale=str(raw.get("rationale") or ""),
        strengths=_string_list(raw.get("strengths")),
        gaps=_string_list(raw.get("gaps")),
        error=str(raw["error"]) if raw.get("error") else None,
    )


def _score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _state_name(state: Any) -> str:
    if hasattr(state, "value"):
        return str(state.value)
    if hasattr(state, "name"):
        return str(state.name)
    return str(state or "")


def _error_message(error: Any) -> str | None:
    if error is None:
        return None
    if isinstance(error, dict):
        message = error.get("message") or error.get("error")
        return str(message) if message else str(error)
    message = getattr(error, "message", None)
    return str(message) if message else str(error)


def _attr_or_key(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


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
