import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlmodel import col

from app.models.application import Application
from app.models.job import Job
from app.models.llm_match_batch import LLMMatchBatch, LLMMatchBatchItem
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.scheduler.tasks import run_daily_maintenance
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


@pytest.mark.asyncio
async def test_maintenance_application_trim_skips_batch_item_references(db_session):
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        email=f"maintenance-{uuid.uuid4()}@local",
        is_active=True,
        is_verified=True,
    )
    profile = UserProfile(
        user_id=user.id,
        email=user.email,
        search_active=False,
        search_expires_at=None,
    )
    db_session.add_all([user, profile])
    await db_session.commit()
    await db_session.refresh(profile)

    jobs: list[Job] = []
    apps: list[Application] = []
    for index in range(502):
        job = Job(
            source="greenhouse",
            external_id=f"maintenance-trim-{uuid.uuid4()}",
            title=f"Role {index}",
            company_name="ExampleCo",
            apply_url=f"https://example.com/jobs/{index}",
        )
        created_at = now - timedelta(days=10 - index / 1000)
        app = Application(
            job_id=job.id,
            profile_id=profile.id,
            status="pending_review",
            match_score=0.7,
            created_at=created_at,
            updated_at=created_at,
        )
        jobs.append(job)
        apps.append(app)
    db_session.add_all(jobs)
    await db_session.commit()
    db_session.add_all(apps)
    await db_session.commit()

    old_unreferenced = apps[0]
    old_referenced = apps[1]
    batch = LLMMatchBatch(
        profile_id=profile.id,
        provider="gemini",
        model="test-model",
        prompt_version="test",
        status="done",
    )
    batch_item = LLMMatchBatchItem(
        batch_id=batch.id,
        application_id=old_referenced.id,
        provider_request_key="req-1",
        provider_request_position=0,
        request_hash="hash-1",
        status="imported",
    )
    db_session.add_all([batch, batch_item])
    await db_session.commit()

    result = await run_daily_maintenance()

    remaining_ids = set(
        (
            await db_session.execute(
                sa.select(col(Application.id)).where(col(Application.profile_id) == profile.id)
            )
        ).scalars()
    )
    assert result["applications_trimmed"] == 1
    assert old_unreferenced.id not in remaining_ids
    assert old_referenced.id in remaining_ids
    assert len(remaining_ids) == 501


def test_maintenance_handler_registers():
    assert isinstance(HANDLERS["maintenance"], MaintenanceHandler)
