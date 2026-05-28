"""Integration tests for the application lifecycle: open → applied."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401
from app.models.application import Application
from app.models.job import Job


@pytest.fixture
async def client(patch_settings, asyncpg_url):
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def sample_application(db_session, seeded_user) -> Application:
    _, profile = seeded_user
    job = Job(
        source="greenhouse",
        external_id="x-1",
        title="Backend Engineer",
        company_name="Acme",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    application = Application(job_id=job.id, profile_id=profile.id, status="pending_review")
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)
    return application


@pytest.mark.asyncio
async def test_submit_endpoint_is_gone(client, seeded_user, auth_headers, sample_application):
    r = await client.post(
        f"/api/applications/{sample_application.id}/submit",
        headers=auth_headers,
    )
    # FastAPI returns 404 when no path matches, or 405 when a path matches
    # but not the method. Either confirms the /submit handler is gone.
    assert r.status_code in (404, 405), r.text


@pytest.mark.asyncio
async def test_mark_applied_transitions_status(
    client, seeded_user, auth_headers, sample_application, db_session
):
    r = await client.post(
        f"/api/applications/{sample_application.id}/mark-applied",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "applied"
    assert body["applied_at"] is not None

    await db_session.refresh(sample_application)
    assert sample_application.status == "applied"
    assert sample_application.applied_at is not None
    event = (
        await db_session.execute(
            text(
                """
                SELECT event_type, subject_type, subject_id, source
                FROM engagement_events
                """
            )
        )
    ).one()
    assert dict(event._mapping) == {
        "event_type": "application_applied",
        "subject_type": "application",
        "subject_id": sample_application.id,
        "source": "api",
    }


@pytest.mark.asyncio
async def test_mark_applied_idempotent(
    client, seeded_user, auth_headers, sample_application, db_session
):
    # First call
    r1 = await client.post(
        f"/api/applications/{sample_application.id}/mark-applied",
        headers=auth_headers,
    )
    assert r1.status_code == 200
    first_at = r1.json()["applied_at"]

    # Second call
    r2 = await client.post(
        f"/api/applications/{sample_application.id}/mark-applied",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    # applied_at should not change on subsequent calls
    assert r2.json()["applied_at"] == first_at
    count = (
        await db_session.execute(
            text(
                """
                SELECT count(*)
                FROM engagement_events
                WHERE event_type = 'application_applied'
                  AND subject_id = :application_id
                """
            ),
            {"application_id": sample_application.id},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_review_patch_accepts_pending_review_for_undo(
    client, seeded_user, auth_headers, sample_application, db_session
):
    """PATCH accepts 'pending_review' so the UI's 'Move back to pending' can
    roll back an accidental Open posting click. applied_at is cleared on
    the transition."""
    # First move the application to applied so applied_at is set.
    r = await client.post(
        f"/api/applications/{sample_application.id}/mark-applied",
        headers=auth_headers,
    )
    assert r.status_code == 200
    await db_session.refresh(sample_application)
    assert sample_application.applied_at is not None

    # Now PATCH back to pending_review.
    r2 = await client.patch(
        f"/api/applications/{sample_application.id}",
        json={"status": "pending_review"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "pending_review"

    await db_session.refresh(sample_application)
    assert sample_application.status == "pending_review"
    assert sample_application.applied_at is None
