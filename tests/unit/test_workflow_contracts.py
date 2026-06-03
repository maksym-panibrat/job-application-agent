import uuid

import pytest

from app.contracts.workflow import (
    ApplicationStatus,
    GenerationStatus,
    JobType,
    batch_match_dedupe_key,
    cover_letter_dedupe_key,
    fetch_slug_dedupe_key,
    match_dedupe_key,
)


def test_status_and_job_type_values_are_stable_wire_contracts():
    assert ApplicationStatus.PENDING_REVIEW == "pending_review"
    assert ApplicationStatus.AUTO_REJECTED == "auto_rejected"
    assert ApplicationStatus.DISMISSED == "dismissed"
    assert ApplicationStatus.APPLIED == "applied"

    assert GenerationStatus.NONE == "none"
    assert GenerationStatus.PENDING == "pending"
    assert GenerationStatus.GENERATING == "generating"
    assert GenerationStatus.READY == "ready"
    assert GenerationStatus.FAILED == "failed"

    assert JobType.FETCH_SLUG == "fetch-slug"
    assert JobType.MATCH == "match"
    assert JobType.BATCH_MATCH == "batch-match"
    assert JobType.GENERATE_COVER_LETTER == "generate-cover-letter"
    assert JobType.MAINTENANCE == "maintenance"


def test_dedupe_key_builders_keep_queue_contracts_in_one_place():
    aid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    pid = uuid.UUID("00000000-0000-0000-0000-000000000002")

    assert fetch_slug_dedupe_key("greenhouse", "openai") == "fetch-slug:greenhouse:openai"
    assert match_dedupe_key(aid) == f"match:{aid}"
    assert batch_match_dedupe_key(pid) == f"batch-match:{pid}"
    assert cover_letter_dedupe_key(aid) == f"generate-cover-letter:{aid}"


def test_fetch_slug_dedupe_key_rejects_separator_in_parts():
    with pytest.raises(ValueError):
        fetch_slug_dedupe_key("green:house", "openai")
    with pytest.raises(ValueError):
        fetch_slug_dedupe_key("greenhouse", "open:ai")
