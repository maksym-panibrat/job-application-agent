import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus


@pytest.fixture
async def authed_client(auth_headers):
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=auth_headers,
    ) as client:
        yield client


async def _seed_application(
    db_session,
    profile: UserProfile,
    *,
    generation_status: str = "none",
    cover_letter_content: str | None = None,
    generated_at: datetime | None = None,
) -> Application:
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
        generation_status=generation_status,
        cover_letter_content=cover_letter_content,
        generated_at=generated_at,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


async def _seed_queue_row(
    db_session,
    app: Application,
    *,
    status: WorkQueueStatus,
    claimed_at: datetime | None = None,
    completed_at: datetime | None = None,
    last_error: str | None = None,
) -> WorkQueue:
    row = WorkQueue(
        job_type="generate-cover-letter",
        payload={"application_id": str(app.id)},
        status=status,
        claimed_at=claimed_at,
        completed_at=completed_at,
        claimed_by="w1" if status == WorkQueueStatus.IN_PROGRESS else None,
        attempts=1,
        last_error=last_error,
        dedupe_key=f"generate-cover-letter:{app.id}",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_status_no_generation_returns_404(
    authed_client,
    db_session,
    seeded_user,
):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="none")

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_status_pending_no_queue_row(authed_client, db_session, seeded_user):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="pending")

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_status_pending_with_pending_queue(authed_client, db_session, seeded_user):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="pending")
    await _seed_queue_row(db_session, app, status=WorkQueueStatus.PENDING)

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_status_pending_with_in_progress_queue_shows_generating(
    authed_client,
    db_session,
    seeded_user,
):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="pending")
    await _seed_queue_row(
        db_session,
        app,
        status=WorkQueueStatus.IN_PROGRESS,
        claimed_at=datetime(2026, 5, 12, tzinfo=UTC),
    )

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    assert response.json()["status"] == "generating"


@pytest.mark.asyncio
async def test_status_generating_with_in_progress_queue(
    authed_client,
    db_session,
    seeded_user,
):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="generating")
    await _seed_queue_row(
        db_session,
        app,
        status=WorkQueueStatus.IN_PROGRESS,
        claimed_at=datetime(2026, 5, 12, tzinfo=UTC),
    )

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    assert response.json()["status"] == "generating"


@pytest.mark.asyncio
async def test_status_ready_wins_over_in_progress(
    authed_client,
    db_session,
    seeded_user,
):
    _, profile = seeded_user
    app = await _seed_application(
        db_session,
        profile,
        generation_status="ready",
        cover_letter_content="X",
        generated_at=datetime(2026, 5, 12, 1, tzinfo=UTC),
    )
    await _seed_queue_row(
        db_session,
        app,
        status=WorkQueueStatus.IN_PROGRESS,
        claimed_at=datetime(2026, 5, 12, tzinfo=UTC),
    )

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "completed_at" in body


@pytest.mark.asyncio
async def test_status_failed_with_failed_queue(authed_client, db_session, seeded_user):
    _, profile = seeded_user
    app = await _seed_application(db_session, profile, generation_status="failed")
    await _seed_queue_row(
        db_session,
        app,
        status=WorkQueueStatus.FAILED,
        completed_at=datetime(2026, 5, 12, 1, tzinfo=UTC),
        last_error="boom",
    )

    response = await authed_client.get(f"/api/applications/{app.id}/cover-letter/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "boom"
