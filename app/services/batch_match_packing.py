from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

TRUNCATION_SUFFIX = "\n\n[Description truncated for batch]"
REQUEST_OVERHEAD_CHARS = 1800
JOB_OVERHEAD_CHARS = 320


@dataclass(frozen=True)
class BatchJobContext:
    application_id: uuid.UUID
    title: str
    company: str
    location: str | None
    workplace_type: str | None
    description: str


@dataclass(frozen=True)
class PackedProviderRequest:
    request_key: str
    jobs: list[BatchJobContext]
    estimated_chars: int


def build_request_hash(
    *,
    prompt_version: str,
    model: str,
    profile_text: str,
    job: BatchJobContext,
) -> str:
    payload = {
        "prompt_version": prompt_version,
        "model": model,
        "profile_text": profile_text,
        "job": {
            "application_id": str(job.application_id),
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "workplace_type": job.workplace_type,
            "description": job.description,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def estimate_request_chars(*, profile_text: str, jobs: list[BatchJobContext]) -> int:
    fixed = REQUEST_OVERHEAD_CHARS + len(profile_text)
    per_job = 0
    for job in jobs:
        per_job += JOB_OVERHEAD_CHARS
        per_job += len(str(job.application_id))
        per_job += len(job.title)
        per_job += len(job.company)
        per_job += len(job.location or "unspecified")
        per_job += len(job.workplace_type or "unspecified")
        per_job += len(job.description)
    return fixed + per_job


def _truncate_to_budget(job: BatchJobContext, max_description_chars: int) -> BatchJobContext:
    if len(job.description) <= max_description_chars:
        return job
    available_chars = max(0, max_description_chars)
    marker = TRUNCATION_SUFFIX[:available_chars]
    prefix_chars = available_chars - len(marker)
    return BatchJobContext(
        application_id=job.application_id,
        title=job.title,
        company=job.company,
        location=job.location,
        workplace_type=job.workplace_type,
        description=job.description[:prefix_chars] + marker,
    )


def _truncate_job_to_request_budget(
    *,
    profile_text: str,
    job: BatchJobContext,
    max_request_chars: int,
) -> BatchJobContext:
    empty_description_job = BatchJobContext(
        application_id=job.application_id,
        title=job.title,
        company=job.company,
        location=job.location,
        workplace_type=job.workplace_type,
        description="",
    )
    non_description_chars = estimate_request_chars(
        profile_text=profile_text,
        jobs=[empty_description_job],
    )
    if non_description_chars > max_request_chars:
        raise ValueError("max_request_chars cannot fit a single job without description")
    max_description_chars = max_request_chars - non_description_chars
    if len(job.description) > max_description_chars:
        max_description_chars = max(0, max_description_chars)
    return _truncate_to_budget(job, max_description_chars)


def pack_provider_requests(
    *,
    profile_text: str,
    jobs: list[BatchJobContext],
    max_apps_per_request: int,
    max_request_chars: int,
) -> list[PackedProviderRequest]:
    if max_apps_per_request < 1:
        raise ValueError("max_apps_per_request must be at least 1")

    groups: list[PackedProviderRequest] = []
    current: list[BatchJobContext] = []

    def flush() -> None:
        if not current:
            return
        groups.append(
            PackedProviderRequest(
                request_key=f"request-{len(groups) + 1:04d}",
                jobs=list(current),
                estimated_chars=estimate_request_chars(profile_text=profile_text, jobs=current),
            )
        )
        current.clear()

    for original_job in jobs:
        job = _truncate_job_to_request_budget(
            profile_text=profile_text,
            job=original_job,
            max_request_chars=max_request_chars,
        )
        candidate = [*current, job]
        if current and (
            len(candidate) > max_apps_per_request
            or estimate_request_chars(profile_text=profile_text, jobs=candidate)
            > max_request_chars
        ):
            flush()
        current.append(job)
    flush()
    return groups
