"""Integration test for run_match_queue cron worker."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_match_queue
from app.services import match_queue_service


async def _seed_profile(db_session, *slugs: str) -> UserProfile:
    """Seed a User + UserProfile (FK constraint requires the user row first)."""
    user = User(id=uuid.uuid4(), email=f"mqcron-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": list(slugs)},
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_run_match_queue_drains_pending(db_session):
    await _seed_profile(db_session, "airbnb")
    job = Job(
        source="greenhouse_board",
        external_id="x-1",
        title="Engineer",
        company_name=slug_to_company_name("airbnb"),
        apply_url="https://x",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)

    # Patch the LangGraph build_graph to return a passing score
    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        return {
            "scores": [
                ScoreResult(
                    application_id=state["jobs"][0]["application_id"],
                    score=0.9,
                    rationale="great fit",
                    strengths=["python"],
                    gaps=[],
                )
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue()

    assert result["attempted"] == 1
    assert result["succeeded"] == 1

    # run_match_queue commits via separate sessions; expire to re-read
    db_session.expire_all()
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 1
    assert apps[0].match_status == "matched"
    assert apps[0].match_score == 0.9
