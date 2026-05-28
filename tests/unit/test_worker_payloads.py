import uuid

import pytest
from pydantic import ValidationError

from app.worker.payloads import (
    FetchSlugPayload,
    GenerateCoverLetterPayload,
    MatchPayload,
)


def test_fetch_slug_payload():
    p = FetchSlugPayload(provider="greenhouse", slug="openai")
    assert p.provider == "greenhouse"
    assert p.slug == "openai"
    with pytest.raises(ValidationError):
        FetchSlugPayload(provider="greenhouse")


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
