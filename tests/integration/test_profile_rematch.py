"""POST /api/profile/rematch flips eligible apps back to pending_match for re-scoring."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile


async def _make_job(db_session, external_id: str = None) -> Job:
    job = Job(
        source="greenhouse_board",
        external_id=external_id or str(uuid.uuid4()),
        title="Engineer",
        company_name="Co",
        apply_url="https://example.com/apply",
        description_md="A role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def _scored_app(job_id, profile_id, *, status: str) -> Application:
    return Application(
        job_id=job_id,
        profile_id=profile_id,
        status=status,
        match_status="matched",
        match_score=0.8,
        match_summary="Old summary",
        match_rationale="Old rationale",
        match_strengths=["A"],
        match_gaps=["B"],
    )


@pytest.mark.asyncio
async def test_rematch_resets_eligible_apps_only(db_session, auth_headers, seeded_user):
    """Resets pending_review+scored and auto_rejected+scored.

    Leaves dismissed/applied/queued untouched.
    """
    from app.main import app as fastapi_app

    _user, profile = seeded_user

    # Eligible: pending_review with a score
    j1 = await _make_job(db_session, "rm-1")
    a_pending = _scored_app(j1.id, profile.id, status="pending_review")
    # Eligible: auto_rejected with a score
    j2 = await _make_job(db_session, "rm-2")
    a_rejected = _scored_app(j2.id, profile.id, status="auto_rejected")
    # NOT eligible: dismissed (user decision)
    j3 = await _make_job(db_session, "rm-3")
    a_dismissed = _scored_app(j3.id, profile.id, status="dismissed")
    # NOT eligible: applied (user decision)
    j4 = await _make_job(db_session, "rm-4")
    a_applied = _scored_app(j4.id, profile.id, status="applied")
    # NOT eligible: still queued (no match_score)
    j5 = await _make_job(db_session, "rm-5")
    a_queued = Application(
        job_id=j5.id,
        profile_id=profile.id,
        status="pending_review",
        match_status="pending_match",
        match_score=None,
    )

    db_session.add_all([a_pending, a_rejected, a_dismissed, a_applied, a_queued])
    await db_session.commit()
    for a in (a_pending, a_rejected, a_dismissed, a_applied, a_queued):
        await db_session.refresh(a)
    pending_id = a_pending.id
    rejected_id = a_rejected.id
    dismissed_id = a_dismissed.id
    applied_id = a_applied.id
    queued_id = a_queued.id

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/profile/rematch", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reset"] == 2  # only the two scored+pending/auto_rejected apps

    # Verify state per app
    for app_id, label in [(pending_id, "pending"), (rejected_id, "rejected")]:
        app = (
            await db_session.execute(select(Application).where(Application.id == app_id))
        ).scalar_one()
        await db_session.refresh(app)
        assert app.match_status == "pending_match", f"{label}: match_status not reset"
        assert app.match_score is None, f"{label}: match_score not cleared"
        assert app.match_summary is None, f"{label}: summary not cleared"
        assert app.match_rationale is None, f"{label}: rationale not cleared"
        assert app.match_strengths == [], f"{label}: strengths not cleared"
        assert app.match_gaps == [], f"{label}: gaps not cleared"
        assert app.status == "pending_review", f"{label}: status not lifted to pending_review"
        assert app.match_queued_at is not None, f"{label}: match_queued_at not set"

    # Untouched apps
    for app_id, expected_status in [
        (dismissed_id, "dismissed"),
        (applied_id, "applied"),
    ]:
        app = (
            await db_session.execute(select(Application).where(Application.id == app_id))
        ).scalar_one()
        await db_session.refresh(app)
        assert app.status == expected_status
        assert app.match_status == "matched"  # unchanged
        assert app.match_score == 0.8

    # Already-queued unchanged
    queued = (
        await db_session.execute(select(Application).where(Application.id == queued_id))
    ).scalar_one()
    await db_session.refresh(queued)
    assert queued.match_status == "pending_match"
    assert queued.match_score is None  # was already None


@pytest.mark.asyncio
async def test_rematch_isolates_other_profiles(db_session, auth_headers, seeded_user):
    """Caller's apps are reset; another user's apps are untouched."""
    from app.main import app as fastapi_app

    _user, profile = seeded_user

    # Other user with their own scored app
    other_user = User(id=uuid.uuid4(), email=f"other-{uuid.uuid4()}@test.com")
    db_session.add(other_user)
    await db_session.commit()
    other_profile = UserProfile(user_id=other_user.id, email=other_user.email)
    db_session.add(other_profile)
    await db_session.commit()
    await db_session.refresh(other_profile)

    j_mine = await _make_job(db_session, "iso-mine")
    j_theirs = await _make_job(db_session, "iso-theirs")
    mine = _scored_app(j_mine.id, profile.id, status="pending_review")
    theirs = _scored_app(j_theirs.id, other_profile.id, status="pending_review")
    db_session.add_all([mine, theirs])
    await db_session.commit()
    await db_session.refresh(mine)
    await db_session.refresh(theirs)
    mine_id = mine.id
    theirs_id = theirs.id

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/profile/rematch", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["reset"] == 1  # only mine

    mine_after = (
        await db_session.execute(select(Application).where(Application.id == mine_id))
    ).scalar_one()
    theirs_after = (
        await db_session.execute(select(Application).where(Application.id == theirs_id))
    ).scalar_one()
    await db_session.refresh(mine_after)
    await db_session.refresh(theirs_after)
    assert mine_after.match_status == "pending_match"
    assert theirs_after.match_status == "matched"  # untouched
    assert theirs_after.match_score == 0.8
