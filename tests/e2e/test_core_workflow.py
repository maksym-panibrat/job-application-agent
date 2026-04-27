"""
E2E tests — core job application workflow:
  upload resume → sync → match → generate → review → dismiss
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.base import JobData

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_data(idx: int = 0) -> JobData:
    return JobData(
        external_id=f"test-job-{idx:03d}",
        title=f"Senior Python Engineer #{idx}",
        company_name="Acme Corp",
        location="New York",
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{idx}",
        ats_type="greenhouse",
        supports_api_apply=True,
        description_md="We need a Python expert for distributed systems work.",
    )


def _mock_greenhouse_source(jobs: list[JobData]) -> MagicMock:
    """Mock GreenhouseBoardSource — returns the given jobs from search()."""
    source = MagicMock()
    source.source_name = "greenhouse_board"
    source.search = AsyncMock(return_value=(jobs, None))
    return source


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(test_app):
    """The /health endpoint returns 200."""
    resp = await test_app.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_get_profile_returns_default(test_app):
    """GET /api/profile returns the auto-created dev profile."""
    resp = await test_app.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["target_roles"] == [] or isinstance(data["target_roles"], list)


@pytest.mark.asyncio
async def test_upload_resume(test_app):
    """Uploading a plain-text resume populates base_resume_md."""
    resume_content = b"# Jane Doe\n\n## Experience\nBackend Engineer at Acme (2020-2024)"
    resp = await test_app.post(
        "/api/profile/upload",
        files={"file": ("resume.txt", io.BytesIO(resume_content), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_resume_md"] is not None
    assert len(data["base_resume_md"]) > 0


@pytest.mark.asyncio
async def test_update_profile(test_app):
    """PATCH /api/profile updates target_roles."""
    resp = await test_app.patch(
        "/api/profile",
        json={"target_roles": ["Backend Engineer", "Senior SWE"], "remote_ok": True},
    )
    assert resp.status_code == 200

    # Verify the update persisted
    get_resp = await test_app.get("/api/profile")
    assert "Backend Engineer" in get_resp.json()["target_roles"]


@pytest.mark.asyncio
async def test_job_sync_with_mocked_source(test_app, monkeypatch):
    """
    POST /api/jobs/sync → syncs jobs from mocked GreenhouseBoardSource → new jobs returned.
    The background scoring task is also mocked to avoid LLM calls.
    """
    # Set target_company_slugs so sync_profile doesn't early-return
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    jobs = [_make_job_data(i) for i in range(3)]
    mock_source = _mock_greenhouse_source(jobs)

    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource", lambda: mock_source
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_jobs"] == 3
    assert data["updated_jobs"] == 0


@pytest.mark.asyncio
async def test_sync_then_list_applications_empty_without_scoring(test_app, monkeypatch):
    """After sync (no scoring), applications list is empty."""
    await test_app.patch(
        "/api/profile",
        json={"target_company_slugs": {"greenhouse": ["acme"]}},
    )

    jobs = [_make_job_data()]
    mock_source = _mock_greenhouse_source(jobs)

    monkeypatch.setattr(
        "app.services.job_sync_service.GreenhouseBoardSource", lambda: mock_source
    )
    monkeypatch.setattr("app.api.jobs._score_after_sync", AsyncMock())

    await test_app.post("/api/jobs/sync")

    resp = await test_app.get("/api/applications")
    assert resp.status_code == 200
    assert resp.json() == []  # no Applications created until scoring runs


@pytest.mark.asyncio
async def test_dismiss_application(test_app, monkeypatch):
    """
    After scoring creates an Application, PATCH /{id} with dismissed works.
    We directly create an application via the DB for simplicity.
    """
    import uuid

    from app.database import get_session_factory
    from app.models.application import Application
    from app.models.job import Job
    from app.models.user_profile import UserProfile

    factory = get_session_factory()
    async with factory() as session:
        # Retrieve the seeded profile via API (dependency override resolves to it)
        profile_resp = await test_app.get("/api/profile")
        profile_id = uuid.UUID(profile_resp.json()["id"])

        from sqlmodel import select

        profile_result = await session.execute(
            select(UserProfile).where(UserProfile.id == profile_id)
        )
        profile = profile_result.scalar_one()

        job = Job(
            source="test",
            external_id="e2e-job-001",
            title="E2E Test Job",
            company_name="Test Corp",
            apply_url="https://example.com/apply",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        app_row = Application(
            job_id=job.id,
            profile_id=profile.id,
            match_score=0.85,
            match_rationale="Strong Python background.",
        )
        session.add(app_row)
        await session.commit()
        await session.refresh(app_row)
        app_id = str(app_row.id)

    # Dismiss via API
    resp = await test_app.patch(f"/api/applications/{app_id}", json={"status": "dismissed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"

    # Dismissed app should not appear in pending_review list
    list_resp = await test_app.get("/api/applications?status=pending_review")
    ids = [a["id"] for a in list_resp.json()]
    assert app_id not in ids


@pytest.mark.asyncio
async def test_toggle_search_pause(test_app):
    """PATCH /api/profile/search pauses the search."""
    resp = await test_app.patch("/api/profile/search", json={"search_active": False})
    assert resp.status_code == 200
    assert resp.json()["search_active"] is False

    # Resume
    resp2 = await test_app.patch("/api/profile/search", json={"search_active": True})
    assert resp2.status_code == 200
    assert resp2.json()["search_active"] is True
    assert resp2.json()["search_expires_at"] is not None
