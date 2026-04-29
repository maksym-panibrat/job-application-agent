"""
E2E tests — core API surface: health, profile, resume upload, dismiss, pause.

Sync/match scenarios live in tests/integration/test_sync_queue_cron.py,
test_match_queue_cron.py, test_jobs_endpoint.py, and test_sync_status_endpoint.py.
"""

import io

import pytest


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
