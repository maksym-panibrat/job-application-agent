import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.application import Application
from app.models.job import Job


async def _add_application(db_session, profile_id, *, status: str, score: float = 0.8):
    job = Job(
        source="greenhouse",
        external_id=str(uuid.uuid4()),
        title=f"{status} role",
        company_name="Summary Co",
        apply_url=f"https://example.com/{uuid.uuid4()}",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    app = Application(job_id=job.id, profile_id=profile_id, status=status, match_score=score)
    db_session.add(app)
    await db_session.commit()
    return app


@pytest.mark.asyncio
async def test_application_summary_counts_statuses_without_list_limit(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    _user, profile = seeded_user
    for _ in range(22):
        await _add_application(db_session, profile.id, status="pending_review")
    await _add_application(db_session, profile.id, status="auto_rejected", score=0.2)
    await _add_application(db_session, profile.id, status="dismissed")
    await _add_application(db_session, profile.id, status="applied")

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/applications/summary", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "pending_review": 22,
        "auto_rejected": 1,
        "dismissed": 1,
        "applied": 1,
    }
