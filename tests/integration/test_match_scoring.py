"""
Integration tests for match scoring pipeline — real Postgres, mocked LLM.

Tests cover: score persistence for all results, auto_rejected status,
list_applications filtering/ordering, and N+1 elimination via join.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import select

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.match_service import list_applications, score_and_match


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
) -> Job:
    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title=title,
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="A great engineering role.",
        salary=salary,
        posted_at=posted_at,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


def _make_llm_mock(scores: list[float]):
    """Return a mock LLM whose invoke() cycles through the given scores."""
    call_index = [0]

    def fake_invoke(messages, **kwargs):
        i = call_index[0] % len(scores)
        call_index[0] += 1
        score = scores[i]
        resp = MagicMock()
        resp.tool_calls = [
            {
                "name": "record_score",
                "args": {
                    "score": score,
                    "rationale": f"Score is {score}",
                    "strengths": ["relevant experience"],
                    "gaps": ["missing skill"],
                },
            }
        ]
        return resp

    mock_llm = MagicMock()
    mock_llm.invoke = fake_invoke
    mock_bound = MagicMock()
    mock_bound.invoke = fake_invoke
    mock_llm.bind_tools.return_value = mock_bound
    return mock_llm


@pytest.mark.asyncio
async def test_score_and_match_persists_all_scores(db_session):
    """All Application rows get their scores persisted regardless of threshold."""
    profile = await _seed_profile(db_session)
    jobs = [await _seed_job(db_session, title=f"Job {i}") for i in range(3)]

    # Scores: 0.9 (pass), 0.5 (fail), 0.7 (pass)
    mock_llm = _make_llm_mock([0.9, 0.5, 0.7])

    with patch("app.agents.matching_agent.get_llm", return_value=mock_llm):
        scored = await score_and_match(profile, db_session, jobs=jobs)

    assert len(scored) == 2  # only 0.9 and 0.7 pass threshold

    # All 3 applications exist with scores persisted
    result = await db_session.execute(
        select(Application).where(Application.profile_id == profile.id)
    )
    all_apps = result.scalars().all()
    assert len(all_apps) == 3
    assert all(a.match_score is not None for a in all_apps)

    statuses = {a.match_score: a.status for a in all_apps}
    assert statuses[0.5] == "auto_rejected"
    # The passing ones stay pending_review
    for a in all_apps:
        if a.match_score != 0.5:
            assert a.status == "pending_review"


@pytest.mark.asyncio
async def test_list_applications_excludes_auto_rejected(db_session):
    """list_applications(status='pending_review') never returns auto_rejected rows."""
    profile = await _seed_profile(db_session)
    job1 = await _seed_job(db_session, title="Good Match")
    job2 = await _seed_job(db_session, title="Poor Match")
    job3 = await _seed_job(db_session, title="Legacy Zombie")  # null score, pending_review

    # Good match — scored and pending
    app1 = Application(
        job_id=job1.id, profile_id=profile.id, match_score=0.85, match_rationale="Great"
    )
    # Poor match — scored and auto_rejected
    app2 = Application(
        job_id=job2.id, profile_id=profile.id, match_score=0.4,
        status="auto_rejected", match_rationale="Weak",
    )
    # Legacy zombie — null score, pending_review (pre-existing bad data)
    app3 = Application(job_id=job3.id, profile_id=profile.id)

    for a in [app1, app2, app3]:
        db_session.add(a)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session, status="pending_review")
    ids = [str(app.id) for app, _ in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids  # auto_rejected filtered
    assert str(app3.id) not in ids  # null score filtered


@pytest.mark.asyncio
async def test_list_applications_ordering(db_session):
    """Matches ordered: match_score DESC, salary non-null first, posted_at DESC."""
    profile = await _seed_profile(db_session)

    now = datetime.now(UTC)
    job_a = await _seed_job(db_session, "Job A", salary="$100k", posted_at=now - timedelta(days=1))
    job_b = await _seed_job(db_session, "Job B", salary=None, posted_at=now - timedelta(days=2))
    job_c = await _seed_job(db_session, "Job C", salary="$90k", posted_at=now)
    job_d = await _seed_job(db_session, "Job D", salary=None, posted_at=now - timedelta(days=3))

    # Ordering: salary non-null (A, C) before null (B, D), then posted_at desc within group
    score = 0.8
    for job in [job_a, job_b, job_c, job_d]:
        app = Application(
            job_id=job.id, profile_id=profile.id, match_score=score, match_rationale="Good"
        )
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    titles = [job.title for _, job in rows]

    # Both salary non-null jobs should come before null-salary jobs
    null_salary_positions = [i for i, (_, j) in enumerate(rows) if j.salary is None]
    non_null_positions = [i for i, (_, j) in enumerate(rows) if j.salary is not None]
    assert max(non_null_positions) < min(null_salary_positions), (
        f"Non-null salary jobs should all precede null-salary jobs, got order: {titles}"
    )

    # Within non-null salary group: job_c (now) before job_a (now-1day)
    assert titles.index("Job C") < titles.index("Job A")
    # Within null salary group: job_b (now-2days) before job_d (now-3days)
    assert titles.index("Job B") < titles.index("Job D")


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
