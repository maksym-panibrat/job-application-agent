"""
Integration tests for POST /api/applications/{id}/submit endpoint.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401 — registers all SQLModel tables with metadata
from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile

# Matches SINGLE_USER_ID in app/api/deps.py — used when AUTH_ENABLED=false
SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _seed_submit_application(
    db_session,
    *,
    ats_type: str | None = None,
    supports_api_apply: bool = False,
    apply_url: str = "https://example.com/apply",
    custom_answers_structured: dict | None = None,
) -> tuple[Application, Job, UserProfile]:
    """Seed User → UserProfile → Job → Application tied to SINGLE_USER_ID."""
    # Use SINGLE_USER_ID so the API (which authenticates as SINGLE_USER_ID when
    # AUTH_ENABLED=false) finds the application via profile_id ownership check.
    user = User(
        id=SINGLE_USER_ID,
        email="dev@local",
        is_active=True,
        is_verified=True,
        is_superuser=True,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=SINGLE_USER_ID,
        full_name="Jane Doe",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        base_resume_md="# Jane Doe\n\nSoftware Engineer",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="test",
        external_id=str(uuid.uuid4()),
        title="Software Engineer",
        company_name="Acme Corp",
        apply_url=apply_url,
        ats_type=ats_type,
        supports_api_apply=supports_api_apply,
        description_md="Python role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    if custom_answers_structured is not None:
        doc = GeneratedDocument(
            application_id=app_row.id,
            doc_type="custom_answers",
            content_md="",
            structured_content=custom_answers_structured,
        )
        db_session.add(doc)
        await db_session.commit()

    return app_row, job, profile


@pytest.fixture
async def client(patch_settings, asyncpg_url):
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_submit_needs_review_when_unanswered_questions(client, db_session):
    """If custom_answers has empty answers, returns needs_review without setting submitted_at."""
    app_row, _, _ = await _seed_submit_application(
        db_session,
        ats_type=None,
        custom_answers_structured={"Q1": "", "Q2": "Some answer"},
    )

    resp = await client.post(f"/api/applications/{app_row.id}/submit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "needs_review"
    assert "Q1" in data["unanswered_questions"]
    assert "Q2" not in data["unanswered_questions"]

    await db_session.refresh(app_row)
    assert app_row.submitted_at is None
    assert app_row.status != "applied"


@pytest.mark.asyncio
async def test_submit_manual_fallback_when_no_ats(client, db_session):
    """Job with ats_type=None → method=manual, submitted_at set, submission_method=manual."""
    apply_url = "https://example.com/apply/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        ats_type=None,
        apply_url=apply_url,
    )

    resp = await client.post(f"/api/applications/{app_row.id}/submit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "manual"
    assert data["apply_url"] == apply_url

    await db_session.refresh(app_row)
    assert app_row.submitted_at is not None
    assert app_row.submission_method == "manual"


@pytest.mark.asyncio
async def test_submit_greenhouse_api_success(client, db_session):
    """Greenhouse job with supports_api_apply=True → greenhouse submit, sets applied status."""
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {"method": "greenhouse_api", "success": True}
    with patch(
        "app.sources.greenhouse.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit")

    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "greenhouse_api"
    assert data["success"] is True

    await db_session.refresh(app_row)
    assert app_row.status == "applied"
    assert app_row.submission_method == "greenhouse_api"
    assert app_row.submitted_at is not None
