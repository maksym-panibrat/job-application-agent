import uuid

import pytest
import sqlalchemy as sa

from app.database import get_session_factory
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue
from app.services import application_service


async def _seed_job_and_profile(db_session) -> tuple[uuid.UUID, uuid.UUID]:
    user = User(id=uuid.uuid4(), email=f"state-machine-{uuid.uuid4()}@test.com")
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
    return job.id, profile.id


async def _seed_application(db_session, *, generation_status: str = "none") -> uuid.UUID:
    job_id, profile_id = await _seed_job_and_profile(db_session)
    app = Application(
        job_id=job_id,
        profile_id=profile_id,
        generation_status=generation_status,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app.id


async def _generation_status(db_session, app_id: uuid.UUID) -> str:
    return (
        await db_session.execute(
            sa.select(Application.generation_status).where(Application.id == app_id)
        )
    ).scalar_one()


async def _generation_queue_count(db_session, app_id: uuid.UUID) -> int:
    return (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(WorkQueue)
            .where(
                WorkQueue.job_type == "generate-cover-letter",
                WorkQueue.dedupe_key == f"generate-cover-letter:{app_id}",
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_status", ["none", "ready", "failed"])
async def test_flip_to_pending_and_enqueue_allowed_sources(db_session, source_status):
    app_id = await _seed_application(db_session, generation_status=source_status)

    await application_service.flip_to_pending_and_enqueue(
        session_factory=get_session_factory(),
        application_id=app_id,
    )

    assert await _generation_status(db_session, app_id) == "pending"
    assert await _generation_queue_count(db_session, app_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden", ["pending", "generating"])
async def test_flip_to_pending_and_enqueue_rejects_in_flight(db_session, forbidden):
    app_id = await _seed_application(db_session, generation_status=forbidden)

    with pytest.raises(application_service.IllegalTransition):
        await application_service.flip_to_pending_and_enqueue(
            session_factory=get_session_factory(),
            application_id=app_id,
        )
