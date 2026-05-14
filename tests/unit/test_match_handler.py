import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.application import Application
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.handlers import TransientError
from app.worker.handlers.match import MatchHandler


def _match_row(app_id: uuid.UUID) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="match",
        payload={"application_id": app_id},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="w1",
    )


def _application(*, status: str = "pending_review") -> Application:
    return Application(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        status=status,
        match_status="pending_match",
        match_strengths=[],
        match_gaps=[],
    )


def _session_for_app(app: Application) -> AsyncMock:
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = app
    session.execute.return_value = execute_result
    session.add = MagicMock()
    return session


def _score_result(score: float | None) -> dict:
    return {
        "score": score,
        "summary": "remote mismatch",
        "rationale": "office attendance mismatch",
        "strengths": [],
        "gaps": ["Requires recurring office attendance outside target locations"],
    }


@pytest.mark.asyncio
async def test_match_handler_auto_rejects_below_threshold_score():
    app = _application(status="pending_review")
    session = _session_for_app(app)

    handler = MatchHandler()

    with (
        patch("app.agents.matching_agent.score_one", AsyncMock(return_value=_score_result(0.29))),
        patch(
            "app.worker.handlers.match.get_settings",
            return_value=SimpleNamespace(match_score_threshold=0.65),
        ),
    ):
        await handler(session, _match_row(app.id))

    assert app.status == "auto_rejected"
    assert app.match_status == "matched"


@pytest.mark.asyncio
async def test_match_handler_score_none_remains_retryable():
    app = _application(status="pending_review")
    session = _session_for_app(app)

    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock(return_value=_score_result(None))):
        with pytest.raises(TransientError, match="matching score skipped"):
            await handler(session, _match_row(app.id))

    assert app.match_score is None
    assert app.match_status == "pending_match"
    assert app.status == "pending_review"
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_match_handler_preserves_dismissed_status_on_passing_score():
    app = _application(status="dismissed")
    session = _session_for_app(app)

    handler = MatchHandler()

    with (
        patch("app.agents.matching_agent.score_one", AsyncMock(return_value=_score_result(0.92))),
        patch(
            "app.worker.handlers.match.get_settings",
            return_value=SimpleNamespace(match_score_threshold=0.65),
        ),
    ):
        await handler(session, _match_row(app.id))

    assert app.status == "dismissed"
    assert app.match_status == "matched"
    assert app.match_score == 0.92


@pytest.mark.asyncio
async def test_match_handler_preserves_applied_status_on_below_threshold_score():
    app = _application(status="applied")
    session = _session_for_app(app)

    handler = MatchHandler()

    with (
        patch("app.agents.matching_agent.score_one", AsyncMock(return_value=_score_result(0.29))),
        patch(
            "app.worker.handlers.match.get_settings",
            return_value=SimpleNamespace(match_score_threshold=0.65),
        ),
    ):
        await handler(session, _match_row(app.id))

    assert app.status == "applied"
    assert app.match_status == "matched"
    assert app.match_score == 0.29
