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


async def _seed_submit_application(
    db_session,
    profile,  # passed from caller (seeded_user fixture)
    *,
    ats_type: str | None = None,
    supports_api_apply: bool = False,
    apply_url: str = "https://example.com/apply",
    custom_answers_structured: dict | None = None,
) -> tuple[Application, Job, UserProfile]:
    """Seed a Job + Application tied to the given profile."""
    # Top up profile fields the tests rely on
    if not profile.full_name:
        profile.full_name = "Jane Doe"
        profile.first_name = "Jane"
        profile.last_name = "Doe"
        profile.base_resume_md = "# Jane Doe\n\nSoftware Engineer"
        profile.target_roles = ["Software Engineer"]
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
async def test_submit_needs_review_when_unanswered_questions(client, db_session, seeded_user, auth_headers):
    """If custom_answers has empty answers, returns needs_review without setting submitted_at."""
    _, profile = seeded_user
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type=None,
        custom_answers_structured={"Q1": "", "Q2": "Some answer"},
    )

    resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "needs_review"
    assert "Q1" in data["unanswered_questions"]
    assert "Q2" not in data["unanswered_questions"]

    await db_session.refresh(app_row)
    assert app_row.submitted_at is None
    assert app_row.status != "applied"


@pytest.mark.asyncio
async def test_submit_manual_fallback_when_no_ats(client, db_session, seeded_user, auth_headers):
    """Job with ats_type=None → method=manual, submitted_at set, submission_method=manual."""
    _, profile = seeded_user
    apply_url = "https://example.com/apply/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type=None,
        apply_url=apply_url,
    )

    resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "manual"
    assert data["apply_url"] == apply_url

    await db_session.refresh(app_row)
    assert app_row.submitted_at is not None
    assert app_row.submission_method == "manual"


@pytest.mark.asyncio
async def test_submit_greenhouse_api_success(client, db_session, seeded_user, auth_headers):
    """Greenhouse job with supports_api_apply=True → greenhouse submit, sets applied status."""
    _, profile = seeded_user
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {"method": "greenhouse_api", "success": True}
    with patch(
        "app.sources.greenhouse.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "greenhouse_api"
    assert data["success"] is True

    await db_session.refresh(app_row)
    assert app_row.status == "applied"
    assert app_row.submission_method == "greenhouse_api"
    assert app_row.submitted_at is not None


@pytest.mark.asyncio
async def test_submit_greenhouse_422_returns_http_400(client, db_session, seeded_user, auth_headers):
    """Greenhouse 422 → endpoint returns HTTP 400 with failure_reason."""
    _, profile = seeded_user
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {
        "method": "greenhouse_api",
        "success": False,
        "status_code": 422,
        "error": "HTTP 422: Unprocessable Entity",
    }
    with patch(
        "app.sources.greenhouse.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)

    assert resp.status_code == 400
    data = resp.json()
    assert data["success"] is False
    assert "failure_reason" in data
    assert "422" in data["failure_reason"]


@pytest.mark.asyncio
async def test_submit_greenhouse_503_returns_http_502(client, db_session, seeded_user, auth_headers):
    """Greenhouse 503 → endpoint returns HTTP 502 with failure_reason."""
    _, profile = seeded_user
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {
        "method": "greenhouse_api",
        "success": False,
        "status_code": 503,
        "error": "HTTP 503: Service Unavailable",
    }
    with patch(
        "app.sources.greenhouse.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)

    assert resp.status_code == 502
    data = resp.json()
    assert data["success"] is False
    assert "failure_reason" in data


@pytest.mark.asyncio
async def test_submit_greenhouse_unreachable_returns_http_502(client, db_session, seeded_user, auth_headers):
    """Greenhouse network error (status_code=None) → HTTP 502 + failure_reason=ats_unreachable."""
    _, profile = seeded_user
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {
        "method": "greenhouse_api",
        "success": False,
        "status_code": None,
        "error": "timed out",
    }
    with patch(
        "app.sources.greenhouse.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)

    assert resp.status_code == 502
    data = resp.json()
    assert data["failure_reason"] == "ats_unreachable"


@pytest.mark.asyncio
async def test_submit_lever_500_returns_http_502(client, db_session, seeded_user, auth_headers):
    """Lever 500 → endpoint returns HTTP 502."""
    _, profile = seeded_user
    apply_url = "https://jobs.lever.co/acme/abc-1234"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="lever",
        apply_url=apply_url,
    )

    mock_result = {
        "method": "lever_api",
        "success": False,
        "status_code": 500,
        "body": "Internal Server Error",
    }
    with patch(
        "app.sources.lever_submit.try_submit",
        new=AsyncMock(return_value=mock_result),
    ):
        resp = await client.post(f"/api/applications/{app_row.id}/submit", headers=auth_headers)

    assert resp.status_code == 502
    data = resp.json()
    assert "failure_reason" in data


@pytest.mark.asyncio
async def test_submit_dry_run_smoke_user_short_circuits(client, db_session):
    """X-Smoke-DryRun: true from smoke user → HTTP 200, method=dry_run, no ATS call."""
    import uuid as uuid_mod

    from app.api.applications import SMOKE_USER_ID

    apply_url = "https://boards.greenhouse.io/exampleco/jobs/99999"

    # Override the authenticated user to be the smoke user
    user = User(
        id=SMOKE_USER_ID,
        email="smoke@local",
        is_active=True,
        is_verified=True,
        is_superuser=True,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=SMOKE_USER_ID,
        full_name="Smoke User",
        first_name="Smoke",
        last_name="User",
        email="smoke@example.com",
        base_resume_md="# Smoke",
        target_roles=["Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="test",
        external_id=str(uuid_mod.uuid4()),
        title="Smoke Role",
        company_name="Smoke Corp",
        apply_url=apply_url,
        ats_type="greenhouse",
        supports_api_apply=True,
        description_md="Smoke test role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    # Override the dep so this request is authenticated as the smoke user
    from app.api.deps import get_current_profile
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_current_profile] = lambda: profile

    ats_mock = AsyncMock()
    with patch("app.sources.greenhouse.try_submit", new=ats_mock):
        resp = await client.post(
            f"/api/applications/{app_row.id}/submit",
            headers={"X-Smoke-DryRun": "true"},
        )

    del fastapi_app.dependency_overrides[get_current_profile]

    assert resp.status_code == 200
    data = resp.json()
    assert data["method"] == "dry_run"
    assert data["would_submit"] is True
    assert data["ats_type"] == "greenhouse"
    ats_mock.assert_not_called()

    await db_session.refresh(app_row)
    assert app_row.submission_method == "dry_run"
    assert app_row.submitted_at is not None


@pytest.mark.asyncio
async def test_submit_dry_run_non_smoke_user_ignored(client, db_session, seeded_user, auth_headers):
    """X-Smoke-DryRun: true from a normal user → header silently ignored, ATS called normally."""
    _, profile = seeded_user
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/77777"
    app_row, _, _ = await _seed_submit_application(
        db_session,
        profile,
        ats_type="greenhouse",
        supports_api_apply=True,
        apply_url=apply_url,
    )

    mock_result = {"method": "greenhouse_api", "success": True, "status_code": 200}
    ats_mock = AsyncMock(return_value=mock_result)
    with patch("app.sources.greenhouse.try_submit", new=ats_mock):
        resp = await client.post(
            f"/api/applications/{app_row.id}/submit",
            headers={**auth_headers, "X-Smoke-DryRun": "true"},
        )

    assert resp.status_code == 200
    data = resp.json()
    # Normal user never gets dry_run — ATS was called
    assert data["method"] == "greenhouse_api"
    ats_mock.assert_called_once()
