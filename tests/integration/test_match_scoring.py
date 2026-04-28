"""
Integration tests for match scoring pipeline — real Postgres, mocked LLM.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models.application import Application
from app.services.match_service import (
    get_or_create_application,
    list_applications,
    score_and_match,
)
from tests.conftest import patch_llm


@pytest.mark.asyncio
async def test_score_and_match_persists_all_scores(seeded_profile, seeded_job_factory, db_session):
    """All Application rows get their scores persisted regardless of threshold."""
    jobs = [await seeded_job_factory(title=f"Job {i}") for i in range(3)]

    # Scores: 0.9 (pass), 0.5 (fail), 0.7 (pass)
    responses = [
        '{"score": 0.9, "rationale": "Excellent match", "strengths": ["Python"], "gaps": []}',
        '{"score": 0.5, "rationale": "Weak match", "strengths": [], "gaps": ["Many missing"]}',
        '{"score": 0.7, "rationale": "Good match", "strengths": ["FastAPI"], "gaps": ["Go"]}',
    ]
    with patch_llm("app.agents.matching_agent", responses):
        scored = await score_and_match(seeded_profile, db_session, jobs=jobs)

    assert len(scored) == 2  # only 0.9 and 0.7 pass threshold

    result = await db_session.execute(
        select(Application).where(Application.profile_id == seeded_profile.id)
    )
    all_apps = result.scalars().all()
    assert len(all_apps) == 3
    assert all(a.match_score is not None for a in all_apps)

    statuses = {a.match_score: a.status for a in all_apps}
    assert statuses[0.5] == "auto_rejected"
    for a in all_apps:
        if a.match_score != 0.5:
            assert a.status == "pending_review"


@pytest.mark.asyncio
async def test_list_applications_excludes_auto_rejected(
    seeded_profile, seeded_job_factory, db_session
):
    """list_applications(status='pending_review') never returns auto_rejected rows."""
    job1 = await seeded_job_factory(title="Good Match")
    job2 = await seeded_job_factory(title="Poor Match")
    job3 = await seeded_job_factory(title="Legacy Zombie")

    app1 = Application(
        job_id=job1.id, profile_id=seeded_profile.id, match_score=0.85, match_rationale="Great"
    )
    app2 = Application(
        job_id=job2.id,
        profile_id=seeded_profile.id,
        match_score=0.4,
        status="auto_rejected",
        match_rationale="Weak",
    )
    app3 = Application(job_id=job3.id, profile_id=seeded_profile.id)

    for a in [app1, app2, app3]:
        db_session.add(a)
    await db_session.commit()

    rows = await list_applications(seeded_profile.id, db_session, status="pending_review")
    ids = [str(app.id) for app, _ in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids
    assert str(app3.id) not in ids


@pytest.mark.asyncio
async def test_list_applications_ordering(seeded_profile, seeded_job_factory, db_session):
    """Matches ordered: match_score DESC, salary non-null first, posted_at DESC."""
    now = datetime.now(UTC)
    job_a = await seeded_job_factory(
        title="Job A", salary="$100k", posted_at=now - timedelta(days=1)
    )
    job_b = await seeded_job_factory(title="Job B", salary=None, posted_at=now - timedelta(days=2))
    job_c = await seeded_job_factory(title="Job C", salary="$90k", posted_at=now)
    job_d = await seeded_job_factory(title="Job D", salary=None, posted_at=now - timedelta(days=3))

    score = 0.8
    for job in [job_a, job_b, job_c, job_d]:
        app = Application(
            job_id=job.id, profile_id=seeded_profile.id, match_score=score, match_rationale="Good"
        )
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(seeded_profile.id, db_session)
    titles = [job.title for _, job in rows]

    null_salary_positions = [i for i, (_, j) in enumerate(rows) if j.salary is None]
    non_null_positions = [i for i, (_, j) in enumerate(rows) if j.salary is not None]
    assert max(non_null_positions) < min(null_salary_positions), (
        f"Non-null salary jobs should all precede null-salary jobs, got order: {titles}"
    )

    assert titles.index("Job C") < titles.index("Job A")
    assert titles.index("Job B") < titles.index("Job D")


@pytest.mark.asyncio
async def test_score_and_match_does_not_persist_none_scores(
    seeded_profile, seeded_job_factory, db_session
):
    """Issue #46: scoring failures (rate limits, quota) should NOT poison the
    pool. When matching_agent returns score=None, score_and_match must leave
    match_score NULL so the Application is re-eligible on the next sync."""
    from unittest.mock import AsyncMock, patch

    from app.agents.matching_agent import ScoreResult

    job = await seeded_job_factory(title="Will fail to score")

    # Patch the matching graph to return a "scoring skipped" ScoreResult
    # (score=None) instead of a real LLM-derived score.
    fake_graph = AsyncMock()

    async def fake_ainvoke(state, config=None):
        app_id = state["jobs"][0]["application_id"]
        return {
            "scores": [
                ScoreResult(
                    application_id=app_id,
                    score=None,
                    rationale="Skipped: API rate limit exceeded after retries",
                    strengths=[],
                    gaps=[],
                )
            ]
        }

    fake_graph.ainvoke = fake_ainvoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        await score_and_match(seeded_profile, db_session, jobs=[job])

    result = await db_session.execute(
        select(Application).where(Application.profile_id == seeded_profile.id)
    )
    apps = result.scalars().all()
    assert len(apps) == 1
    # Failed-scoring rows must not be marked auto_rejected; match_score stays None
    # so the next sync's matched_ids filter does NOT exclude this Application.
    assert apps[0].match_score is None
    assert apps[0].status != "auto_rejected"


@pytest.mark.asyncio
async def test_score_and_match_picks_unscored_jobs_when_pool_is_largely_scored(
    seeded_profile, seeded_job_factory, db_session
):
    """
    Issue #45: matching used to LIMIT before filtering out already-scored jobs,
    so once 20 jobs were scored every subsequent run yielded 0 fresh candidates.

    Seed more jobs than the batch limit, pre-score the first batch, and assert
    that score_and_match still finds the unscored ones.
    """
    from app.config import get_settings

    settings = get_settings()
    batch = settings.matching_jobs_per_batch  # default 20
    extra = 5

    # First `batch` jobs: pre-create scored Applications so they appear in matched_ids.
    pre_scored_jobs = [await seeded_job_factory(title=f"Pre-scored {i}") for i in range(batch)]
    for j in pre_scored_jobs:
        db_session.add(
            Application(
                job_id=j.id,
                profile_id=seeded_profile.id,
                match_score=0.8,
                match_rationale="seeded",
            )
        )
    await db_session.commit()

    # Next `extra` jobs: unscored, must be picked up.
    fresh_jobs = [await seeded_job_factory(title=f"Fresh {i}") for i in range(extra)]
    fresh_ids = {j.id for j in fresh_jobs}

    responses = [
        '{"score": 0.85, "rationale": "Good", "strengths": ["Python"], "gaps": []}'
    ] * extra
    with patch_llm("app.agents.matching_agent", responses):
        await score_and_match(seeded_profile, db_session)

    result = await db_session.execute(
        select(Application).where(
            Application.profile_id == seeded_profile.id,
            Application.match_rationale != "seeded",
        )
    )
    new_apps = result.scalars().all()
    new_job_ids = {a.job_id for a in new_apps}

    assert new_job_ids == fresh_ids, (
        f"Expected the {extra} fresh jobs to be scored, "
        f"but got {len(new_job_ids)} new applications. "
        "The LIMIT is being applied before the matched_ids filter."
    )


@pytest.mark.asyncio
async def test_get_or_create_application_is_idempotent(
    seeded_profile, seeded_job_factory, db_session
):
    """Second call with the same (job_id, profile_id) returns None instead of
    a duplicate Application — protects the unique constraint and makes
    score_and_match safe to re-run."""
    job = await seeded_job_factory()

    first = await get_or_create_application(job.id, seeded_profile.id, db_session)
    assert first is not None

    second = await get_or_create_application(job.id, seeded_profile.id, db_session)
    assert second is None

    rows = (
        (
            await db_session.execute(
                select(Application).where(
                    Application.job_id == job.id, Application.profile_id == seeded_profile.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_applications_returns_job_data(seeded_profile, seeded_job_factory, db_session):
    """list_applications returns (Application, Job) tuples — no N+1 queries needed."""
    job = await seeded_job_factory(title="Python Engineer", salary="$120k")

    app = Application(
        job_id=job.id, profile_id=seeded_profile.id, match_score=0.8, match_rationale="Great fit"
    )
    db_session.add(app)
    await db_session.commit()

    rows = await list_applications(seeded_profile.id, db_session)
    assert len(rows) == 1

    returned_app, returned_job = rows[0]
    assert returned_app.id == app.id
    assert returned_job.id == job.id
    assert returned_job.title == "Python Engineer"
    assert returned_job.salary == "$120k"
