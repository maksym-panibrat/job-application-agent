"""Integration tests for the application lifecycle: open → applied."""

import pytest
from httpx import ASGITransport, AsyncClient
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
        source="greenhouse_board",
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
    assert r.status_code == 404, r.text


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


@pytest.mark.asyncio
async def test_mark_applied_idempotent(
    client, seeded_user, auth_headers, sample_application
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
