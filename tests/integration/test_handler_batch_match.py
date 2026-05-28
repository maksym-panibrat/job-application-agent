import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.services.batch_match_provider import FakeBatchMatchProvider
from app.worker.handlers import HANDLERS


def _batch_match_row(profile_id: uuid.UUID) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="batch-match",
        payload={"profile_id": str(profile_id)},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="w1",
    )


def test_batch_match_handler_registers():
    from app.worker.handlers.batch_match import BatchMatchHandler

    assert isinstance(HANDLERS["batch-match"], BatchMatchHandler)


@pytest.mark.asyncio
async def test_batch_match_handler_calls_service_with_parsed_profile_id(db_session):
    from app.services.batch_match_service import BatchMatchTickResult
    from app.worker.handlers.batch_match import BatchMatchHandler

    profile_id = uuid.uuid4()
    result = BatchMatchTickResult(
        selected=1,
        deterministic_rejected=2,
        submitted=3,
        imported=4,
        retryable_failed=5,
        terminal_failed=6,
        requeued=True,
    )

    with patch(
        "app.worker.handlers.batch_match.run_batch_match_tick",
        AsyncMock(return_value=result),
    ) as mock_tick, patch(
        "app.worker.handlers.batch_match.log.ainfo",
        AsyncMock(),
    ) as mock_log:
        await BatchMatchHandler()(db_session, _batch_match_row(profile_id))

    assert mock_tick.call_count == 1
    _, kwargs = mock_tick.call_args
    assert kwargs["profile_id"] == profile_id
    assert isinstance(kwargs["provider"], FakeBatchMatchProvider)
    assert kwargs["provider"].ready is False
    mock_log.assert_awaited_once_with(
        "worker.batch_match.done",
        profile_id=str(profile_id),
        selected=1,
        deterministic_rejected=2,
        submitted=3,
        imported=4,
        retryable_failed=5,
        terminal_failed=6,
        requeued=True,
    )
