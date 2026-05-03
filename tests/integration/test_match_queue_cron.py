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


@pytest.mark.asyncio
async def test_run_match_queue_caps_jobs_per_profile_per_tick(db_session):
    """A single profile must not own more than `max_per_profile` jobs in one
    score_and_match call. With batch_size=100 concentrated on one profile and
    slow Gemini latency, a single LangGraph batch can exceed Cloud Run's 300s
    wall (one-off HTTP 504 in /internal/cron/process-match-queue, 2026-05-02).
    Unprocessed apps stay pending_match with claimed_at set; the 300s lease
    in match_queue_service.next_batch makes them re-eligible next tick."""
    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id

    # Seed 8 jobs + 8 pending_match Applications for the same profile
    jobs = []
    for i in range(8):
        job = Job(
            source="greenhouse_board",
            external_id=f"cap-{i}",
            title=f"Engineer {i}",
            company_name=slug_to_company_name("airbnb"),
            apply_url=f"https://x/{i}",
            is_active=True,
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.commit()
    for j in jobs:
        await db_session.refresh(j)
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        # Stub: score every job that was sent into the graph (but only those)
        return {
            "scores": [
                ScoreResult(
                    application_id=jc["application_id"],
                    score=0.9,
                    summary="cap-test",
                    rationale="cap-test",
                    strengths=[],
                    gaps=[],
                )
                for jc in state["jobs"]
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue(max_per_profile=5)

    # Cap: only 5 of the 8 claimed apps were sent to score_and_match this tick.
    # The other 3 stay pending_match with claimed_at set (300s lease).
    assert result["attempted"] == 8, "all 8 were claimed by next_batch"
    assert result["succeeded"] == 5, "exactly max_per_profile (5) were scored"
    assert result["deferred"] == 3, "3 deferred to a later tick by the per-profile cap"

    db_session.expire_all()
    matched = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "matched",
                )
            )
        )
        .scalars()
        .all()
    )
    pending = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "pending_match",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(matched) == 5
    assert len(pending) == 3
    # Deferred apps must remain claimed (claimed_at set) so the next tick's
    # next_batch call doesn't re-claim them inside the 300s lease window.
    for app in pending:
        assert app.match_claimed_at is not None, (
            "deferred apps must remain claimed for the 300s lease window"
        )
