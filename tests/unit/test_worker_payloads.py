import uuid

import pytest
from pydantic import ValidationError

from app.worker.payloads import (
    BatchMatchPayload,
    FetchSlugPayload,
    GenerateCoverLetterPayload,
    MaintenancePayload,
    MatchPayload,
)


def test_fetch_slug_payload():
    p = FetchSlugPayload(provider="greenhouse", slug="openai")
    assert p.provider == "greenhouse"
    assert p.slug == "openai"
    assert p.batch_match_max_items is None
    with pytest.raises(ValidationError):
        FetchSlugPayload(provider="greenhouse")


def test_fetch_slug_payload_accepts_batch_match_limit():
    p = FetchSlugPayload(
        provider="greenhouse",
        slug="openai",
        batch_match_max_items=50,
    )

    assert p.batch_match_max_items == 50


def test_match_payload_requires_application_id():
    aid = uuid.uuid4()
    p = MatchPayload(application_id=aid)
    assert p.application_id == aid
    with pytest.raises(ValidationError):
        MatchPayload()


def test_generate_cover_letter_payload():
    aid = uuid.uuid4()
    p = GenerateCoverLetterPayload(application_id=aid)
    assert p.application_id == aid


def test_maintenance_payload_optional_date():
    p = MaintenancePayload()
    assert p.date is None
    p2 = MaintenancePayload(date="2026-05-12")
    assert p2.date == "2026-05-12"


def test_batch_match_payload_requires_profile_id():
    with pytest.raises(ValidationError):
        BatchMatchPayload()


def test_batch_match_payload_parses_profile_id():
    profile_id = uuid.uuid4()

    payload = BatchMatchPayload(profile_id=str(profile_id), max_items=50)

    assert payload.profile_id == profile_id
    assert payload.max_items == 50
