"""Integration tests for server-authored active engagement events."""

import io
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job


async def _events(db_session):
    result = await db_session.execute(
        text(
            """
            SELECT event_type, subject_type, subject_id, source, metadata
            FROM engagement_events
            ORDER BY occurred_at, id
            """
        )
    )
    return [dict(row._mapping) for row in result]


async def _seed_company(db_session, name: str) -> Company:
    company = Company(
        canonical_name=name,
        normalized_key=f"{name.lower()}-{uuid.uuid4()}",
        provider_slugs={"greenhouse": f"{name.lower()}-{uuid.uuid4()}"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


async def _seed_application(db_session, profile, *, status: str = "pending_review") -> Application:
    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Acme",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    application = Application(job_id=job.id, profile_id=profile.id, status=status)
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)
    return application


@pytest.mark.asyncio
async def test_profile_patch_records_profile_update_and_company_follow(
    db_session, auth_headers, seeded_user
):
    from app.main import app

    _, profile = seeded_user
    company = await _seed_company(db_session, "Linear")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/profile",
            json={"full_name": "Test User", "target_company_ids": [str(company.id)]},
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "profile_updated",
            "subject_type": "profile",
            "subject_id": profile.id,
            "source": "api",
            "metadata": {},
        },
        {
            "event_type": "company_followed",
            "subject_type": "company",
            "subject_id": company.id,
            "source": "api",
            "metadata": {},
        },
    ]


@pytest.mark.asyncio
async def test_profile_patch_removing_company_records_company_unfollowed(
    db_session, auth_headers, seeded_user
):
    from app.main import app

    _, profile = seeded_user
    company = await _seed_company(db_session, "Stripe")
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/profile",
            json={"target_company_ids": []},
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "company_unfollowed",
            "subject_type": "company",
            "subject_id": company.id,
            "source": "api",
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_resume_upload_records_resume_uploaded(
    db_session, auth_headers, seeded_user, monkeypatch
):
    from app.main import app
    from app.services import profile_service

    _, profile = seeded_user

    async def fake_extract_profile_from_resume(_resume_md):
        return {}

    monkeypatch.setattr(
        profile_service, "extract_profile_from_resume", fake_extract_profile_from_resume
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/profile/upload",
            files={"file": ("resume.txt", io.BytesIO(b"# Test User"), "text/plain")},
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "resume_uploaded",
            "subject_type": "profile",
            "subject_id": profile.id,
            "source": "api",
            "metadata": {"extraction_status": "ok"},
        }
    ]


@pytest.mark.asyncio
async def test_search_resume_records_only_when_resuming(db_session, auth_headers, seeded_user):
    from app.main import app

    _, profile = seeded_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pause = await client.patch(
            "/api/profile/search",
            json={"search_active": False},
            headers=auth_headers,
        )
        resume = await client.patch(
            "/api/profile/search",
            json={"search_active": True},
            headers=auth_headers,
        )

    assert pause.status_code == 200, pause.text
    assert resume.status_code == 200, resume.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "search_resumed",
            "subject_type": "profile",
            "subject_id": profile.id,
            "source": "api",
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_application_dismissed_records_engagement(db_session, auth_headers, seeded_user):
    from app.main import app

    _, profile = seeded_user
    application = await _seed_application(db_session, profile)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/applications/{application.id}",
            json={"status": "dismissed"},
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "application_dismissed",
            "subject_type": "application",
            "subject_id": application.id,
            "source": "api",
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_application_applied_records_for_patch_and_mark_applied_without_duplicate(
    db_session, auth_headers, seeded_user
):
    from app.main import app

    _, profile = seeded_user
    patch_application = await _seed_application(db_session, profile)
    post_application = await _seed_application(db_session, profile)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patch_response = await client.patch(
            f"/api/applications/{patch_application.id}",
            json={"status": "applied"},
            headers=auth_headers,
        )
        first_post = await client.post(
            f"/api/applications/{post_application.id}/mark-applied",
            headers=auth_headers,
        )
        second_post = await client.post(
            f"/api/applications/{post_application.id}/mark-applied",
            headers=auth_headers,
        )

    assert patch_response.status_code == 200, patch_response.text
    assert first_post.status_code == 200, first_post.text
    assert second_post.status_code == 200, second_post.text
    rows = await _events(db_session)
    assert rows == [
        {
            "event_type": "application_applied",
            "subject_type": "application",
            "subject_id": patch_application.id,
            "source": "api",
            "metadata": {},
        },
        {
            "event_type": "application_applied",
            "subject_type": "application",
            "subject_id": post_application.id,
            "source": "api",
            "metadata": {},
        },
    ]
