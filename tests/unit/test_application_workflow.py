import uuid
from datetime import UTC, datetime

import pytest

from app.contracts.workflow import ApplicationStatus, GenerationStatus
from app.models.application import Application
from app.services.application_workflow import (
    IllegalApplicationTransition,
    mark_generation_failed,
    mark_generation_ready,
    mark_scored,
    mark_user_decision,
    request_generation,
)


def _application() -> Application:
    return Application(job_id=uuid.uuid4(), profile_id=uuid.uuid4())


def test_mark_scored_sets_reviewable_or_rejected_status():
    app = _application()

    mark_scored(app, score=0.8, threshold=0.7)
    assert app.status == ApplicationStatus.PENDING_REVIEW
    assert app.match_score == 0.8

    mark_scored(app, score=0.2, threshold=0.7)
    assert app.status == ApplicationStatus.AUTO_REJECTED
    assert app.match_score == 0.2


def test_user_decision_transitions_manage_applied_timestamp():
    app = _application()
    now = datetime(2026, 6, 1, tzinfo=UTC)

    mark_user_decision(app, ApplicationStatus.APPLIED, now=now)
    assert app.status == ApplicationStatus.APPLIED
    assert app.applied_at == now

    mark_user_decision(app, ApplicationStatus.PENDING_REVIEW, now=now)
    assert app.status == ApplicationStatus.PENDING_REVIEW
    assert app.applied_at is None


def test_user_decision_rejects_auto_rejected_as_direct_user_action():
    app = _application()

    with pytest.raises(IllegalApplicationTransition):
        mark_user_decision(app, ApplicationStatus.AUTO_REJECTED)


def test_generation_transitions():
    app = _application()
    assert request_generation(app) == GenerationStatus.PENDING
    assert app.generation_status == GenerationStatus.PENDING

    with pytest.raises(IllegalApplicationTransition):
        request_generation(app)

    mark_generation_ready(app, content="hello", now=datetime(2026, 6, 1, tzinfo=UTC))
    assert app.generation_status == GenerationStatus.READY
    assert app.cover_letter_content == "hello"

    request_generation(app)
    mark_generation_failed(app)
    assert app.generation_status == GenerationStatus.FAILED
