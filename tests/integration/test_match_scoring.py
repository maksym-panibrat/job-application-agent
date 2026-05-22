"""
Integration tests for match scoring pipeline — real Postgres, mocked LLM.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlmodel import select

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import match_service
from app.services.match_service import list_applications, score_and_match
from tests.conftest import patch_llm


async def _ensure_company(db_session, slug: str) -> Company:
    existing = (
        await db_session.execute(sa.select(Company).where(Company.normalized_key == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    company = Company(
        canonical_name=slug_to_company_name(slug),
        normalized_key=slug,
        provider_slugs={"greenhouse": slug},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


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
    source: str = "greenhouse",
    company_name: str = "Acme Corp",
) -> Job:
    job = Job(
        source=source,
        external_id=str(uuid.uuid4()),
        title=title,
        company_name=company_name,
        apply_url="https://example.com/apply",
        description="A great engineering role.",
        location="Remote",
        workplace_type="remote",
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
    ids = [str(row[0]) for row in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids
    assert str(app3.id) not in ids


@pytest.mark.asyncio
async def test_list_applications_ordering(db_session):
    """Matches ordered: match_score DESC, posted_at DESC, then salary present."""
    profile = await _seed_profile(db_session)

    now = datetime.now(UTC)
    job_a = await _seed_job(
        db_session,
        "High Score Older Salary",
        salary="$100k",
        posted_at=now - timedelta(days=3),
    )
    job_b = await _seed_job(db_session, "High Score Recent No Salary", salary=None, posted_at=now)
    job_c = await _seed_job(
        db_session,
        "Lower Score Newest Salary",
        salary="$90k",
        posted_at=now + timedelta(days=1),
    )
    job_d = await _seed_job(db_session, "High Score Recent Salary", salary="$120k", posted_at=now)

    apps = [
        (job_a, 0.9, now),
        (job_b, 0.9, now + timedelta(seconds=2)),
        (job_c, 0.8, now + timedelta(seconds=3)),
        (job_d, 0.9, now - timedelta(seconds=1)),
    ]
    for job, score, created_at in apps:
        app = Application(
            job_id=job.id,
            profile_id=profile.id,
            match_score=score,
            match_rationale="Good",
            created_at=created_at,
        )
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    titles = [row[10] for row in rows]

    assert titles == [
        "High Score Recent Salary",
        "High Score Recent No Salary",
        "High Score Older Salary",
        "Lower Score Newest Salary",
    ]


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
    # Match by company_id rather than legacy company_name slug — the candidate
    # query now filters Job.company_id.in_(profile.target_company_ids).
    company = await _ensure_company(db_session, "acme-corp")
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()

    async def _seed_acme_job(title: str) -> Job:
        job = await _seed_job(db_session, title=title)
        job.company_id = company.id
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
        return job

    # First `batch` jobs: pre-create scored Applications so they appear in matched_ids.
    pre_scored_jobs = [await _seed_acme_job(title=f"Pre-scored {i}") for i in range(batch)]
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
    fresh_jobs = [await _seed_acme_job(title=f"Fresh {i}") for i in range(extra)]
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
async def test_list_applications_returns_projected_job_data(db_session):
    """list_applications returns projected application/job tuples without ORM-wide job rows."""
    profile = await _seed_profile(db_session)
    job = await _seed_job(db_session, title="Python Engineer", salary="$120k")

    app = Application(
        job_id=job.id, profile_id=profile.id, match_score=0.8, match_rationale="Great fit"
    )
    db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    assert len(rows) == 1

    row = rows[0]
    assert row[0] == app.id
    assert row[9] == job.id
    assert row[10] == "Python Engineer"
    assert row[14] == "$120k"


@pytest.mark.asyncio
async def test_score_and_match_filters_by_profile_slugs(db_session):
    """A profile that follows only Airbnb must NOT see Stripe jobs as candidates,
    even if Stripe jobs exist in the global pool from other users (spec 2026-04-28)."""
    airbnb_co = await _ensure_company(db_session, "airbnb")
    stripe_co = await _ensure_company(db_session, "stripe")
    airbnb_job = Job(
        source="greenhouse",
        external_id="a-1",
        title="X",
        company_name="Airbnb",
        company_id=airbnb_co.id,
        apply_url="https://x",
        is_active=True,
    )
    stripe_job = Job(
        source="greenhouse",
        external_id="s-1",
        title="Y",
        company_name="Stripe",
        company_id=stripe_co.id,
        apply_url="https://y",
        is_active=True,
    )
    db_session.add_all([airbnb_job, stripe_job])
    user = User(id=uuid.uuid4(), email=f"slugtest-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[airbnb_co.id],
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
async def test_score_and_match_persists_match_score(db_session):
    """After score_and_match persists a score, match_score becomes the scored
    predicate used to avoid duplicate scoring."""
    from app.agents.matching_agent import ScoreResult
    from app.models.application import Application
    from app.models.user import User

    airbnb_co = await _ensure_company(db_session, "airbnb")
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
        target_company_ids=[airbnb_co.id],
    )
    db_session.add(profile)
    job = Job(
        source="greenhouse",
        external_id="m-1",
        title="Eng",
        company_name="Airbnb",
        company_id=airbnb_co.id,
        apply_url="https://x",
        location="Remote",
        workplace_type="remote",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await db_session.refresh(profile)

    # Pre-create an Application row for this job/profile.
    app = Application(
        job_id=job.id,
        profile_id=profile.id,
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


@pytest.mark.asyncio
async def test_score_and_match_persists_summary_and_uses_location(db_session):
    """Scored Application gets match_summary populated and rationale stays for audit."""
    profile = await _seed_profile(db_session)
    profile.target_locations = ["Berlin"]
    db_session.add(profile)
    await db_session.commit()

    job = Job(
        source="greenhouse",
        external_id=str(uuid.uuid4()),
        title="Senior Backend Engineer",
        company_name="Test Co",
        location="Berlin, Germany",
        workplace_type="hybrid",
        description_raw="<p>5+ yrs Python required.</p>",
        description="5+ yrs Python required.",
        apply_url="https://example.com/apply/ms",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    responses = [
        '{"score": 0.85, "summary": "Senior backend, Python, hybrid Berlin.", '
        '"rationale": "Strong stack fit", "strengths": ["5+ yrs Python"], '
        '"gaps": ["Hybrid Berlin, candidate based in CA"]}'
    ]
    with patch_llm("app.agents.matching_agent", responses):
        await score_and_match(profile, db_session, jobs=[job])

    result = await db_session.execute(select(Application).where(Application.job_id == job.id))
    app = result.scalar_one()
    assert app.match_summary == "Senior backend, Python, hybrid Berlin."
    assert app.match_rationale == "Strong stack fit"
    assert app.match_score == 0.85
    assert "Hybrid Berlin" in (app.match_gaps or [""])[0]
