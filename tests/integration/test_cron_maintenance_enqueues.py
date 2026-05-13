import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.models.work_queue import WorkQueue

CRON_SECRET = "dev-cron-secret"


@pytest.mark.asyncio
async def test_cron_maintenance_enqueues_one_date_deduped_row(db_session):
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/internal/cron/maintenance",
            headers={"X-Cron-Secret": CRON_SECRET},
        )
        second = await client.post(
            "/internal/cron/maintenance",
            headers={"X-Cron-Secret": CRON_SECRET},
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(first.json()["enqueued"]) == 1

    count = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(WorkQueue)
            .where(WorkQueue.job_type == "maintenance")
        )
    ).scalar_one()
    assert count == 1
