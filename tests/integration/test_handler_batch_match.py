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
        follow_up = await BatchMatchHandler()(db_session, _batch_match_row(profile_id))

    assert mock_tick.call_count == 1
    _, kwargs = mock_tick.call_args
    assert kwargs["profile_id"] == profile_id
    assert isinstance(kwargs["provider"], FakeBatchMatchProvider)
    assert kwargs["provider"].ready is False
    assert follow_up is not None
    assert follow_up.job_type == "batch-match"
    assert follow_up.payload == {"profile_id": str(profile_id)}
    assert follow_up.dedupe_key == f"batch-match:{profile_id}"
    assert follow_up.not_before_seconds is not None
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


@pytest.mark.asyncio
async def test_batch_match_handler_returns_follow_up_after_submit(db_session):
    from app.services.batch_match_service import BatchMatchTickResult
    from app.worker.handlers.batch_match import BatchMatchHandler

    profile_id = uuid.uuid4()

    with patch(
        "app.worker.handlers.batch_match.run_batch_match_tick",
        AsyncMock(return_value=BatchMatchTickResult(submitted=1)),
    ):
        follow_up = await BatchMatchHandler()(db_session, _batch_match_row(profile_id))

    assert follow_up is not None
    assert follow_up.job_type == "batch-match"
    assert follow_up.dedupe_key == f"batch-match:{profile_id}"


@pytest.mark.asyncio
async def test_batch_match_handler_no_follow_up_when_no_active_work(db_session):
    from app.services.batch_match_service import BatchMatchTickResult
    from app.worker.handlers.batch_match import BatchMatchHandler

    profile_id = uuid.uuid4()

    with patch(
        "app.worker.handlers.batch_match.run_batch_match_tick",
        AsyncMock(return_value=BatchMatchTickResult()),
    ):
        follow_up = await BatchMatchHandler()(db_session, _batch_match_row(profile_id))

    assert follow_up is None


@pytest.mark.asyncio
async def test_batch_match_handler_terminal_failure_logs(monkeypatch):
    from app.worker.handlers.batch_match import BatchMatchHandler

    profile_id = uuid.uuid4()
    with patch("app.worker.handlers.batch_match.log.awarning", AsyncMock()) as mock_log:
        await BatchMatchHandler().on_terminal_failure(
            object(),
            _batch_match_row(profile_id),
            "boom",
        )

    mock_log.assert_awaited_once_with(
        "worker.batch_match.terminal_failure",
        profile_id=str(profile_id),
        error="boom",
    )
