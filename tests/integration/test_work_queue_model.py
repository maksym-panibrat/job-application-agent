import pytest

from app.models.work_queue import WorkQueue, WorkQueueStatus


@pytest.mark.asyncio
async def test_insert_and_read(db_session):
    row = WorkQueue(job_type="sync-board", payload={"provider": "greenhouse"})
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.id is not None
    assert row.status == WorkQueueStatus.PENDING
    assert row.attempts == 0
    assert row.payload == {"provider": "greenhouse"}
    assert row.claimed_by is None
    assert row.not_before is None
