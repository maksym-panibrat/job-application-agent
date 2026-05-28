import uuid

from app.services.batch_match_packing import (
    BatchJobContext,
    build_request_hash,
    estimate_request_chars,
    pack_provider_requests,
)


def _job(index: int, description: str = "Build APIs") -> BatchJobContext:
    return BatchJobContext(
        application_id=uuid.UUID(int=index),
        title=f"Engineer {index}",
        company="Acme",
        location="Remote - United States",
        workplace_type="remote",
        description=description,
    )


def test_pack_provider_requests_caps_at_ten_apps():
    groups = pack_provider_requests(
        profile_text="Python backend engineer",
        jobs=[_job(i) for i in range(1, 12)],
        max_apps_per_request=10,
        max_request_chars=100000,
    )

    assert [len(group.jobs) for group in groups] == [10, 1]
    assert groups[0].request_key == "request-0001"
    assert groups[1].request_key == "request-0002"


def test_pack_provider_requests_respects_char_budget():
    groups = pack_provider_requests(
        profile_text="Python backend engineer",
        jobs=[_job(1, "A" * 100), _job(2, "B" * 100), _job(3, "C" * 100)],
        max_apps_per_request=10,
        max_request_chars=430,
    )

    assert [len(group.jobs) for group in groups] == [2, 1]


def test_pack_provider_requests_truncates_single_oversized_job_to_budget():
    groups = pack_provider_requests(
        profile_text="Python",
        jobs=[_job(1, "A" * 5000)],
        max_apps_per_request=10,
        max_request_chars=600,
    )

    assert len(groups) == 1
    assert groups[0].estimated_chars <= 600
    assert "[Description truncated for batch]" in groups[0].jobs[0].description


def test_pack_provider_requests_handles_budget_too_small_for_truncation_marker():
    job = _job(1, "A" * 5000)
    empty_description_job = _job(1, "")
    max_request_chars = estimate_request_chars(
        profile_text="Python",
        jobs=[empty_description_job],
    ) + 10

    groups = pack_provider_requests(
        profile_text="Python",
        jobs=[job],
        max_apps_per_request=10,
        max_request_chars=max_request_chars,
    )

    assert len(groups) == 1
    assert groups[0].estimated_chars <= max_request_chars


def test_request_hash_changes_when_context_changes():
    first = build_request_hash(
        prompt_version="batch-match-v1",
        model="gemini-2.5-flash",
        profile_text="Python",
        job=_job(1, "Build APIs"),
    )
    second = build_request_hash(
        prompt_version="batch-match-v1",
        model="gemini-2.5-flash",
        profile_text="Python",
        job=_job(1, "Build ML systems"),
    )

    assert first != second
