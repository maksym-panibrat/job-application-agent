import asyncio

import pytest

from app.database import get_session_factory
from app.observability.queue_depth import _emit_queue_depth_forever
from app.worker.queue_service import enqueue


@pytest.mark.asyncio
async def test_emitter_logs_depth_fields(db_session, monkeypatch):
    await enqueue(db_session, job_type="test", payload={})
    await db_session.commit()

    events = []

    async def capture(event, **fields):
        events.append((event, fields))

    monkeypatch.setattr("app.observability.queue_depth.log.ainfo", capture)

    task = asyncio.create_task(
        _emit_queue_depth_forever(get_session_factory(), interval_s=0.1)
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    depth_events = [event for event in events if event[0] == "api.queue_depth"]
    assert len(depth_events) >= 1
    fields = depth_events[0][1]
    assert "pending" in fields
    assert "eligible_pending" in fields
    assert "in_progress" in fields
    assert "oldest_pending_age_s" in fields
    assert "oldest_in_progress_age_s" in fields
    assert fields["pending"] >= 1
