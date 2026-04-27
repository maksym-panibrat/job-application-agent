"""Integration tests for POST /api/applications/{id}/cover-letter (sync)."""

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
    profile.base_resume_md = "# Test User\n\n5 years of Python and FastAPI experience."
    db_session.add(profile)
    await db_session.commit()

    job = Job(
        source="greenhouse_board",
        external_id="cl-1",
        title="Senior Python Engineer",
        company_name="Acme",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        description_md="Backend role on a distributed-systems team.",
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
async def test_generate_cover_letter_returns_doc_synchronously(
    client, auth_headers, sample_application
):
    r = await client.post(
        f"/api/applications/{sample_application.id}/cover-letter",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "cover_letter"
    assert isinstance(body["content_md"], str) and len(body["content_md"]) > 30
    assert body["generation_model"] is not None


@pytest.mark.asyncio
async def test_resume_endpoint_is_gone(client, auth_headers, sample_application):
    r = await client.post(
        f"/api/applications/{sample_application.id}/resume",
        headers=auth_headers,
        json={"decision": "approve"},
    )
    assert r.status_code in (404, 405), r.text


@pytest.mark.asyncio
async def test_regenerate_endpoint_is_gone(client, auth_headers, sample_application):
    r = await client.post(
        f"/api/applications/{sample_application.id}/regenerate",
        headers=auth_headers,
    )
    assert r.status_code in (404, 405), r.text
