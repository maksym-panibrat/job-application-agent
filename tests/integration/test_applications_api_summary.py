"""GET /api/applications and /api/applications/{id} expose match_summary."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app as fastapi_app
from app.models.application import Application
from app.models.job import Job


@pytest.mark.asyncio
async def test_list_endpoint_includes_match_summary(db_session, auth_headers, seeded_user):
    """List endpoint returns match_summary alongside score/strengths/gaps."""
    _user, profile = seeded_user

    job = Job(
        source="greenhouse_board",
        external_id=str(uuid.uuid4()),
        title="API Test Engineer",
        company_name="API Co",
        apply_url="https://example.com/apply",
        description_md="A role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    db_session.add(
        Application(
            job_id=job.id,
            profile_id=profile.id,
            status="pending_review",
            match_score=0.8,
            match_summary="One-line summary text.",
            match_rationale="Audit text.",
            match_strengths=["Python"],
            match_gaps=["Go"],
        )
    )
    await db_session.commit()

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/applications", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    row = next(r for r in rows if r["match_score"] == 0.8)
    assert row["match_summary"] == "One-line summary text."
    assert row["match_rationale"] == "Audit text."  # still serialized for API audit
