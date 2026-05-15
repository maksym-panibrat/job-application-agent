import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.handlers import HANDLERS
from app.worker.handlers.match import MatchHandler


async def _seed_application(
    db_session,
    *,
    match_score: float | None = None,
    app_status: str = "pending_review",
    profile_locations: list[str] | None = None,
    job_location: str | None = "Remote - United States",
    workplace_type: str | None = None,
    description: str | None = None,
    description_raw: str | None = None,
) -> Application:
    user = User(id=uuid.uuid4(), email=f"match-handler-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    company = Company(
        canonical_name="Airbnb",
        normalized_key=f"airbnb-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "airbnb"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[company.id],
        target_locations=profile_locations or ["San Francisco"],
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Airbnb",
        company_id=company.id,
        location=job_location,
        workplace_type=workplace_type,
        description=description,
        description_raw=description_raw,
        apply_url="https://example.com/job",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        status=app_status,
        match_score=match_score,
        match_strengths=[],
        match_gaps=[],
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


def _match_row(app_id: uuid.UUID) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="match",
        payload={"application_id": app_id},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="w1",
    )


@pytest.mark.asyncio
async def test_match_handler_scores_one_application(db_session):
    app = await _seed_application(db_session)
    app_id = app.id
    handler = MatchHandler()

    with patch(
        "app.agents.matching_agent.score_one",
        AsyncMock(
            return_value={
                "score": 0.85,
                "summary": "good fit",
                "rationale": "strong match",
                "strengths": ["Python"],
                "gaps": ["None"],
            }
        ),
    ) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 1
    assert refreshed.match_score == 0.85
    assert refreshed.match_summary == "good fit"


@pytest.mark.asyncio
async def test_match_handler_prefilter_visible_remote_policy_reject(db_session):
    app = await _seed_application(
        db_session,
        description=(
            "Remote role in the United States, but candidates must work "
            "from the New York, NY office twice a week."
        ),
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "auto_rejected"
    assert refreshed.match_score is not None
    assert refreshed.match_score < 0.3
    assert refreshed.match_summary == (
        "Deterministic mismatch: recurring office attendance requirement"
    )
    assert refreshed.match_rationale == (
        "Requires recurring office attendance outside target locations"
    )
    assert refreshed.match_strengths == []
    assert refreshed.match_gaps == [
        "Requires recurring office attendance outside target locations"
    ]


@pytest.mark.asyncio
async def test_match_handler_prefilter_preserves_user_owned_status(db_session):
    app = await _seed_application(
        db_session,
        app_status="dismissed",
        description="Candidates must work from the New York, NY office twice a week.",
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "dismissed"
    assert refreshed.match_score is not None
    assert refreshed.match_gaps == [
        "Requires recurring office attendance outside target locations"
    ]


@pytest.mark.asyncio
async def test_match_handler_prefilter_visible_non_us_reject(db_session):
    app = await _seed_application(
        db_session,
        job_location="Toronto, Canada",
        workplace_type="remote",
        description="Remote role open to candidates based in Canada.",
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "auto_rejected"
    assert refreshed.match_score is not None
    assert refreshed.match_score < 0.3
    assert refreshed.match_summary == "Deterministic mismatch: non-US position"
    assert refreshed.match_rationale == "Position is not US-based"
    assert refreshed.match_strengths == []
    assert refreshed.match_gaps == ["Position is not US-based"]


@pytest.mark.asyncio
async def test_match_handler_prefilter_non_us_preserves_user_owned_status(db_session):
    app = await _seed_application(
        db_session,
        app_status="applied",
        job_location="Remote",
        workplace_type="remote",
        description="Work from anywhere with a distributed engineering team.",
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "applied"
    assert refreshed.match_score is not None
    assert refreshed.match_summary == "Deterministic mismatch: non-US position"
    assert refreshed.match_gaps == ["Position is not US-based"]


@pytest.mark.asyncio
async def test_match_handler_prefilter_allows_target_location_match(db_session):
    app = await _seed_application(
        db_session,
        profile_locations=["San Francisco"],
        description="Candidates must work from the San Francisco, CA office twice a week.",
    )
    handler = MatchHandler()

    with patch(
        "app.agents.matching_agent.score_one",
        AsyncMock(
            return_value={
                "score": 0.83,
                "summary": "location-compatible hybrid fit",
                "rationale": "Toronto target location matches",
                "strengths": ["Python"],
                "gaps": [],
            }
        ),
    ) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    assert mock_score.call_count == 1


@pytest.mark.asyncio
async def test_match_handler_replay_short_circuits_after_success(db_session):
    app = await _seed_application(db_session)
    app_id = app.id
    handler = MatchHandler()
    row = _match_row(app_id)

    with patch(
        "app.agents.matching_agent.score_one",
        AsyncMock(
            return_value={
                "score": 0.85,
                "summary": "v1",
                "rationale": "strong match",
                "strengths": [],
                "gaps": [],
            }
        ),
    ) as mock_score:
        await handler(db_session, row)
        await db_session.commit()
        await handler(db_session, row)
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 1
    assert refreshed.match_score == 0.85
    assert refreshed.match_summary == "v1"


@pytest.mark.asyncio
async def test_match_terminal_failure_logs_without_domain_state(db_session):
    app = await _seed_application(db_session)
    app_id = app.id
    handler = MatchHandler()
    row = _match_row(app_id)

    from app.database import get_session_factory

    await handler.on_terminal_failure(get_session_factory(), row, "boom")

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert refreshed.match_score is None


def test_match_handler_registers():
    assert isinstance(HANDLERS["match"], MatchHandler)
