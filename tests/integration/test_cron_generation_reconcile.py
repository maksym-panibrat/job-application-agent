import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus

CRON_SECRET = "dev-cron-secret"


async def _seed_pending_application(db_session, *, updated_at: datetime) -> Application:
    user = User(id=uuid.uuid4(), email=f"reconcile-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(user_id=user.id, email=user.email)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Engineer",
        company_name="Co",
        apply_url="https://example.com/job",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        generation_status="pending",
        generation_attempts=0,
        updated_at=updated_at,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


@pytest.mark.asyncio
async def test_generation_reconcile_enqueues_only_orphaned_pending_apps(db_session):
    old = datetime.now(UTC) - timedelta(minutes=10)
    orphan = await _seed_pending_application(db_session, updated_at=old)
    not_orphan = await _seed_pending_application(db_session, updated_at=old)
    recent = await _seed_pending_application(
        db_session,
        updated_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db_session.add(
        WorkQueue(
            job_type="generate-cover-letter",
            payload={"application_id": str(not_orphan.id)},
            status=WorkQueueStatus.PENDING,
            dedupe_key=f"generate-cover-letter:{not_orphan.id}",
        )
    )
    await db_session.commit()

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/cron/generation-reconcile",
            headers={"X-Cron-Secret": CRON_SECRET},
        )

    assert response.status_code == 202
    body = response.json()
    assert len(body["reconciled"]) == 1

    rows = (
        (
            await db_session.execute(
                sa.select(WorkQueue).where(
                    WorkQueue.job_type == "generate-cover-letter"
                )
            )
        )
        .scalars()
        .all()
    )
    dedupe_keys = {row.dedupe_key for row in rows}
    assert f"generate-cover-letter:{orphan.id}" in dedupe_keys
    assert f"generate-cover-letter:{not_orphan.id}" in dedupe_keys
    assert f"generate-cover-letter:{recent.id}" not in dedupe_keys
