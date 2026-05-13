from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.handlers import HANDLERS
from app.worker.handlers.maintenance import MaintenanceHandler


@pytest.mark.asyncio
async def test_maintenance_prunes_old_done_and_failed(db_session):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            WorkQueue(
                job_type="match",
                payload={},
                status=WorkQueueStatus.DONE,
                completed_at=now - timedelta(days=10),
            ),
            WorkQueue(
                job_type="match",
                payload={},
                status=WorkQueueStatus.DONE,
                completed_at=now - timedelta(days=1),
            ),
            WorkQueue(
                job_type="match",
                payload={},
                status=WorkQueueStatus.FAILED,
                completed_at=now - timedelta(days=60),
            ),
            WorkQueue(
                job_type="match",
                payload={},
                status=WorkQueueStatus.FAILED,
                completed_at=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    handler = MaintenanceHandler()
    row = WorkQueue(
        id=999,
        job_type="maintenance",
        payload={"date": "2026-05-12"},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
    )
    await handler(db_session, row)
    await db_session.commit()

    count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(WorkQueue)
            .where(WorkQueue.status.in_([WorkQueueStatus.DONE, WorkQueueStatus.FAILED]))
        )
    ).scalar_one()
    assert count == 2


def test_maintenance_handler_registers():
    assert isinstance(HANDLERS["maintenance"], MaintenanceHandler)
