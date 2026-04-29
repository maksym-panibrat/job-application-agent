"""
Integration tests for match scoring pipeline — real Postgres, mocked LLM.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlmodel import select

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import match_service
from app.services.match_service import list_applications, score_and_match
from tests.conftest import patch_llm


async def _seed_profile(db_session) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"test-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer with 5 years experience.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _seed_job(
    db_session,
    title: str = "Software Engineer",
    salary: str | None = None,
    posted_at: datetime | None = None,
    source: str = "greenhouse_board",
    company_name: str = "Acme Corp",
) -> Job:
    job = Job(
        source=source,
        external_id=str(uuid.uuid4()),
        title=title,
        company_name=company_name,
        apply_url="https://example.com/apply",
        description_md="A great engineering role.",
        salary=salary,
        posted_at=posted_at,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_score_and_match_persists_all_scores(db_session):
    """All Application rows get their scores persisted regardless of threshold."""
    profile = await _seed_profile(db_session)
    jobs = [await _seed_job(db_session, title=f"Job {i}") for i in range(3)]

    # Scores: 0.9 (pass), 0.5 (fail), 0.7 (pass)
    responses = [
        '{"score": 0.9, "rationale": "Excellent match", "strengths": ["Python"], "gaps": []}',
        '{"score": 0.5, "rationale": "Weak match", "strengths": [], "gaps": ["Many missing"]}',
        '{"score": 0.7, "rationale": "Good match", "strengths": ["FastAPI"], "gaps": ["Go"]}',
    ]
    with patch_llm("app.agents.matching_agent", responses):
        scored = await score_and_match(profile, db_session, jobs=jobs)

    assert len(scored) == 2  # only 0.9 and 0.7 pass threshold

    result = await db_session.execute(
        select(Application).where(Application.profile_id == profile.id)
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
async def test_list_applications_excludes_auto_rejected(db_session):
    """list_applications(status='pending_review') never returns auto_rejected rows."""
    profile = await _seed_profile(db_session)
    job1 = await _seed_job(db_session, title="Good Match")
    job2 = await _seed_job(db_session, title="Poor Match")
    job3 = await _seed_job(db_session, title="Legacy Zombie")

    app1 = Application(
        job_id=job1.id, profile_id=profile.id, match_score=0.85, match_rationale="Great"
    )
    app2 = Application(
        job_id=job2.id,
        profile_id=profile.id,
        match_score=0.4,
        status="auto_rejected",
        match_rationale="Weak",
    )
    app3 = Application(job_id=job3.id, profile_id=profile.id)

    for a in [app1, app2, app3]:
        db_session.add(a)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session, status="pending_review")
    ids = [str(app.id) for app, _ in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids
    assert str(app3.id) not in ids


@pytest.mark.asyncio
async def test_list_applications_ordering(db_session):
    """Matches ordered: match_score DESC, salary non-null first, posted_at DESC."""
    profile = await _seed_profile(db_session)

    now = datetime.now(UTC)
    job_a = await _seed_job(db_session, "Job A", salary="$100k", posted_at=now - timedelta(days=1))
    job_b = await _seed_job(db_session, "Job B", salary=None, posted_at=now - timedelta(days=2))
    job_c = await _seed_job(db_session, "Job C", salary="$90k", posted_at=now)
    job_d = await _seed_job(db_session, "Job D", salary=None, posted_at=now - timedelta(days=3))

    score = 0.8
    for job in [job_a, job_b, job_c, job_d]:
        app = Application(
            job_id=job.id, profile_id=profile.id, match_score=score, match_rationale="Good"
        )
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    titles = [job.title for _, job in rows]

    null_salary_positions = [i for i, (_, j) in enumerate(rows) if j.salary is None]
    non_null_positions = [i for i, (_, j) in enumerate(rows) if j.salary is not None]
    assert max(non_null_positions) < min(null_salary_positions), (
        f"Non-null salary jobs should all precede null-salary jobs, got order: {titles}"
    )

    assert titles.index("Job C") < titles.index("Job A")
    assert titles.index("Job B") < titles.index("Job D")


@pytest.mark.asyncio
async def test_score_and_match_does_not_persist_none_scores(db_session):
    """Issue #46: scoring failures (rate limits, quota) should NOT poison the
    pool. When matching_agent returns score=None, score_and_match must leave
    match_score NULL so the Application is re-eligible on the next sync."""
    from unittest.mock import AsyncMock, patch

    from app.agents.matching_agent import ScoreResult

    profile = await _seed_profile(db_session)
    job = await _seed_job(db_session, title="Will fail to score")

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
        await score_and_match(profile, db_session, jobs=[job])

    result = await db_session.execute(
        select(Application).where(Application.profile_id == profile.id)
    )
    apps = result.scalars().all()
    assert len(apps) == 1
    # Failed-scoring rows must not be marked auto_rejected; match_score stays None
    # so the next sync's matched_ids filter does NOT exclude this Application.
    assert apps[0].match_score is None
    assert apps[0].status != "auto_rejected"


@pytest.mark.asyncio
async def test_score_and_match_picks_unscored_jobs_when_pool_is_largely_scored(db_session):
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

    profile = await _seed_profile(db_session)
    # Match the slug for jobs whose company_name == "Acme Corp"
    # (slug_to_company_name("acme-corp") == "Acme Corp").
    profile.target_company_slugs = {"greenhouse": ["acme-corp"]}
    db_session.add(profile)
    await db_session.commit()

    # First `batch` jobs: pre-create scored Applications so they appear in matched_ids.
    pre_scored_jobs = [await _seed_job(db_session, title=f"Pre-scored {i}") for i in range(batch)]
    for j in pre_scored_jobs:
        db_session.add(
            Application(
                job_id=j.id,
                profile_id=profile.id,
                match_score=0.8,
                match_rationale="seeded",
            )
        )
    await db_session.commit()

    # Next `extra` jobs: unscored, must be picked up.
    fresh_jobs = [await _seed_job(db_session, title=f"Fresh {i}") for i in range(extra)]
    fresh_ids = {j.id for j in fresh_jobs}

    responses = [
        '{"score": 0.85, "rationale": "Good", "strengths": ["Python"], "gaps": []}'
    ] * extra
    with patch_llm("app.agents.matching_agent", responses):
        await score_and_match(profile, db_session)

    result = await db_session.execute(
        select(Application).where(
            Application.profile_id == profile.id,
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
async def test_list_applications_returns_job_data(db_session):
    """list_applications returns (Application, Job) tuples — no N+1 queries needed."""
    profile = await _seed_profile(db_session)
    job = await _seed_job(db_session, title="Python Engineer", salary="$120k")

    app = Application(
        job_id=job.id, profile_id=profile.id, match_score=0.8, match_rationale="Great fit"
    )
    db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    assert len(rows) == 1

    returned_app, returned_job = rows[0]
    assert returned_app.id == app.id
    assert returned_job.id == job.id
    assert returned_job.title == "Python Engineer"
    assert returned_job.salary == "$120k"


@pytest.mark.asyncio
async def test_score_and_match_filters_by_profile_slugs(db_session):
    """A profile with only ['airbnb'] must NOT see Stripe jobs as candidates,
    even if Stripe jobs exist in the global pool from other users (spec 2026-04-28)."""
    # Two jobs, two companies
    airbnb_job = Job(
        source="greenhouse_board",
        external_id="a-1",
        title="X",
        company_name="Airbnb",
        apply_url="https://x",
        is_active=True,
    )
    stripe_job = Job(
        source="greenhouse_board",
        external_id="s-1",
        title="Y",
        company_name="Stripe",
        apply_url="https://y",
        is_active=True,
    )
    db_session.add_all([airbnb_job, stripe_job])
    user = User(id=uuid.uuid4(), email=f"slugtest-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": ["airbnb"]},
    )
    db_session.add(profile)
    await db_session.commit()

    # Patch the LangGraph build_graph so we don't actually call an LLM —
    # we only care about which jobs become Application rows.
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"scores": []})
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        await match_service.score_and_match(profile, db_session)

    apps = (
        (
            await db_session.execute(
                sa.select(Application).where(Application.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    job_ids = {a.job_id for a in apps}
    assert airbnb_job.id in job_ids
    assert stripe_job.id not in job_ids


@pytest.mark.asyncio
async def test_score_cached_only_uses_existing_jobs(db_session):
    """score_cached must NOT enqueue any fetches and must respect the slug filter
    and matching_jobs_per_batch cap."""
    user = User(id=uuid.uuid4(), email=f"cached-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": ["airbnb"]},
    )
    db_session.add(profile)
    db_session.add(
        Job(
            source="greenhouse_board",
            external_id="a-2",
            title="Z",
            company_name="Airbnb",
            apply_url="https://z",
            is_active=True,
        )
    )
    await db_session.commit()

    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"scores": []})
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await match_service.score_cached(profile, db_session, cap=20)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_score_and_match_flips_match_status_to_matched(db_session):
    """After score_and_match persists a score, the Application must transition
    out of pending_match so run_match_queue doesn't re-score it."""
    from app.agents.matching_agent import ScoreResult
    from app.models.application import Application
    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="t@t.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": ["airbnb"]},
    )
    db_session.add(profile)
    job = Job(
        source="greenhouse_board",
        external_id="m-1",
        title="Eng",
        company_name="Airbnb",
        apply_url="https://x",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await db_session.refresh(profile)

    # Pre-create an Application in pending_match state (mimicking enqueue_for_interested_profiles)
    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        match_status="pending_match",
        match_queued_at=datetime.now(UTC),
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)

    # Patch the LangGraph to return a score for this application
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(
        return_value={
            "scores": [
                ScoreResult(
                    application_id=str(app.id),
                    score=0.85,
                    rationale="great fit",
                    strengths=["python"],
                    gaps=[],
                )
            ]
        }
    )
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        await match_service.score_and_match(profile, db_session, jobs=[job])

    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app.id))
    ).scalar_one()
    assert refreshed.match_score == 0.85
    assert refreshed.match_status == "matched"
    assert refreshed.match_queued_at is None
    assert refreshed.match_claimed_at is None
