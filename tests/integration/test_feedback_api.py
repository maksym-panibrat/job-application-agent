import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
@pytest.mark.xfail(reason="POST /api/feedback is implemented in Task 2", strict=True)
async def test_feedback_submit_creates_row(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    body = {
        "category": "feature_request",
        "message": "Please let me hide companies I rejected.",
        "diagnostics": {
            "reported_at_client": "2026-05-25T20:15:00.000Z",
            "path": "/matches?status=pending",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 1440, "height": 900},
            "timezone": "America/Los_Angeles",
            "route_context": {},
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] is True
    assert payload["notification_status"] == "not_configured"

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    user, _profile = seeded_user
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(
                    FeedbackReport.user_id == user.id,
                    FeedbackReport.message == body["message"],
                )
            )
        ).scalar_one()

    assert row.user_email == user.email
    assert row.category == "feature_request"
    assert row.message == "Please let me hide companies I rejected."
    assert row.notification_status == "not_configured"
    assert row.notification_error is None
    assert row.diagnostics["path"] == "/matches?status=pending"
