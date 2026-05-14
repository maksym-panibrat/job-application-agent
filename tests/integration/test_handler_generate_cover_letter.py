import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.handlers import HANDLERS
from app.worker.handlers.generate_cover_letter import GenerateCoverLetterHandler


async def _seed_application(
    db_session,
    *,
    generation_status: str = "pending",
    cover_letter_content: str | None = None,
) -> Application:
    user = User(id=uuid.uuid4(), email=f"generate-handler-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(user_id=user.id, email=user.email)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Acme",
        apply_url="https://example.com/job",
        description="Backend role",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        generation_status=generation_status,
        cover_letter_content=cover_letter_content,
        generated_at=datetime.now(UTC) if cover_letter_content else None,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


def _generation_row(app_id: uuid.UUID, *, attempts: int = 1) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="generate-cover-letter",
        payload={"application_id": app_id},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=attempts,
        claimed_by="w1",
    )


@pytest.mark.asyncio
async def test_generate_cover_letter_handler_generates_and_flips_ready(db_session):
    app = await _seed_application(db_session)
    app_id = app.id

    with patch(
        "app.services.application_service.generate_materials_llm",
        AsyncMock(return_value="GENERATED COVER LETTER"),
    ) as mock_llm:
        await GenerateCoverLetterHandler()(db_session, _generation_row(app_id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    doc = (
        await db_session.execute(
            sa.select(GeneratedDocument).where(
                GeneratedDocument.application_id == app_id,
                GeneratedDocument.doc_type == "cover_letter",
            )
        )
    ).scalar_one()
    assert refreshed.generation_status == "ready"
    assert refreshed.cover_letter_content == "GENERATED COVER LETTER"
    assert refreshed.generated_at is not None
    assert doc.content_md == "GENERATED COVER LETTER"
    assert mock_llm.call_count == 1


@pytest.mark.asyncio
async def test_generate_cover_letter_handler_replay_short_circuits_ready_content(db_session):
    app = await _seed_application(
        db_session,
        generation_status="ready",
        cover_letter_content="PRIOR",
    )
    app_id = app.id

    with patch(
        "app.services.application_service.generate_materials_llm",
        AsyncMock(return_value="DIFFERENT"),
    ) as mock_llm:
        await GenerateCoverLetterHandler()(db_session, _generation_row(app_id, attempts=2))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert refreshed.generation_status == "ready"
    assert refreshed.cover_letter_content == "PRIOR"
    assert mock_llm.call_count == 0


@pytest.mark.asyncio
async def test_generate_cover_letter_terminal_failure_marks_domain_failed(db_session):
    app = await _seed_application(db_session)
    app_id = app.id
    handler = GenerateCoverLetterHandler()

    from app.database import get_session_factory

    await handler.on_terminal_failure(get_session_factory(), _generation_row(app_id), "boom")

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert refreshed.generation_status == "failed"


def test_generate_cover_letter_handler_registers():
    assert isinstance(HANDLERS["generate-cover-letter"], GenerateCoverLetterHandler)
