import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.application import Application
from app.models.work_queue import WorkQueue, WorkQueueStatus
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


@pytest.mark.asyncio
async def test_match_handler_auto_rejects_below_threshold_score():
    app = Application(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        status="pending_review",
        match_status="pending_match",
        match_strengths=[],
        match_gaps=[],
    )
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = app
    session.execute.return_value = execute_result
    session.add = MagicMock()

    handler = MatchHandler()

    with (
        patch(
            "app.agents.matching_agent.score_one",
            AsyncMock(
                return_value={
                    "score": 0.29,
                    "summary": "remote mismatch",
                    "rationale": "office attendance mismatch",
                    "strengths": [],
                    "gaps": ["Requires recurring office attendance outside target locations"],
                }
            ),
        ),
        patch(
            "app.worker.handlers.match.get_settings",
            return_value=SimpleNamespace(match_score_threshold=0.65),
        ),
    ):
        await handler(session, _match_row(app.id))

    assert app.status == "auto_rejected"
    assert app.match_status == "matched"
