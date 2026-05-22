"""
Unit tests for match_service score_and_match() threshold logic and logging.

These tests pass jobs explicitly to skip the DB auto-fetch path, and mock
the session and build_graph so no real DB or LLM is needed.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile

_P = "app.services.match_service"
_GET_SKILLS = f"{_P}.profile_service.get_skills"
_GET_EXPS = f"{_P}.profile_service.get_work_experiences"
_GET_OR_CREATE = f"{_P}.get_or_create_application"
_BUILD_GRAPH = "app.agents.matching_agent.build_graph"
_GET_SETTINGS = f"{_P}.get_settings"


def setup_env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")


def _make_profile() -> UserProfile:
    return UserProfile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer.",
        target_roles=["Software Engineer"],
        seniority="senior",
        remote_ok=True,
    )


def _make_job(
    job_id: uuid.UUID | None = None,
    *,
    title: str = "Software Engineer",
    company_name: str = "Acme Corp",
    description: str = "A great job.",
    location: str | None = "Remote",
    workplace_type: str | None = "remote",
) -> Job:
    return Job(
        id=job_id or uuid.uuid4(),
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title=title,
        company_name=company_name,
        apply_url="https://example.com/apply",
        location=location,
        workplace_type=workplace_type,
        description=description,
    )


def _make_application(
    app_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    *,
    status: str = "pending_review",
) -> Application:
    return Application(
        id=app_id or uuid.uuid4(),
        job_id=job_id or uuid.uuid4(),
        profile_id=uuid.uuid4(),
        status=status,
        match_score=None,
        match_rationale=None,
        match_strengths=[],
        match_gaps=[],
    )


def _make_score_result(application_id: str, score: float):
    from app.agents.matching_agent import ScoreResult

    return ScoreResult(
        application_id=application_id,
        score=score,
        rationale=f"Score is {score}",
        strengths=["relevant experience"],
        gaps=["missing skill X"],
    )


def test_remote_policy_caps_remote_only_office_attendance_score():
    from app.services.match_service import apply_remote_policy_to_score

    profile = _make_profile()
    profile.target_locations = []
    job = _make_job(
        description="This role requires minimum 3 days/week in the Toronto office.",
        location="Remote",
        workplace_type="remote",
    )
    score_result = _make_score_result(str(uuid.uuid4()), score=0.92)

    adjusted = apply_remote_policy_to_score(score_result, profile, job, 0.65)

    assert adjusted.score < 0.65
    assert adjusted.score == 0.29
    assert any("office attendance" in gap for gap in adjusted.gaps)
    assert "office attendance" in adjusted.rationale


def test_remote_policy_does_not_cap_matching_target_location():
    from app.services.match_service import apply_remote_policy_to_score

    profile = _make_profile()
    profile.target_locations = ["Toronto"]
    job = _make_job(
        description="This role requires minimum 3 days/week in the Toronto office.",
        location="Remote",
        workplace_type="remote",
    )
    score_result = _make_score_result(str(uuid.uuid4()), score=0.92)
    original_gaps = list(score_result.gaps)

    adjusted = apply_remote_policy_to_score(score_result, profile, job, 0.65)

    assert adjusted.score == 0.92
    assert adjusted.gaps == original_gaps


@pytest.mark.asyncio
async def test_list_applications_filters_out_jobs_older_than_10_days():
    from app.services.match_service import list_applications

    profile_id = uuid.uuid4()
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.tuples.return_value.all.return_value = []
    session.execute.return_value = execute_result

    before = datetime.now(UTC)
    await list_applications(profile_id, session)
    after = datetime.now(UTC)

    stmt = session.execute.call_args.args[0]
    compiled = stmt.compile(compile_kwargs={"literal_binds": False})
    sql = str(compiled)
    cutoff = compiled.params["posted_at_1"]

    assert "jobs.posted_at IS NULL" in sql
    assert "jobs.posted_at >= " in sql
    assert before - timedelta(days=10) <= cutoff <= after - timedelta(days=10)


@pytest.mark.asyncio
async def test_remote_only_city_location_at_threshold_is_auto_rejected():
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id)
    job = _make_job(
        job_id=job_id,
        title="Staff Software Engineer - AI Research Infrastructure",
        company_name="Databricks",
        location="Mountain View, California; New York City, New York; San Francisco, California",
        workplace_type=None,
        description="Build research infrastructure for large-scale AI workloads.",
    )
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.65)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()
    profile.target_locations = []
    profile.remote_ok = True

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=[job])

    assert scored == []
    assert app.status == "auto_rejected"
    assert app.match_score == 0.29
    assert "remote-only profile" in app.match_rationale
    assert any("remote-only profile" in gap for gap in app.match_gaps)


def _make_mock_session(app: MagicMock):
    """Build an AsyncMock session that returns the given app from execute()."""
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = app
    session.execute.return_value = execute_result
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_below_threshold_sets_auto_rejected():
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id)
    job = _make_job(job_id=job_id)
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.4)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=[job])

    assert scored == []
    assert app.status == "auto_rejected"
    assert app.match_score == 0.4
    assert app.match_rationale == "Score is 0.4"
    assert app.match_strengths == ["relevant experience"]
    assert app.match_gaps == ["missing skill X"]


@pytest.mark.asyncio
async def test_below_threshold_preserves_dismissed_status():
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id, status="dismissed")
    job = _make_job(job_id=job_id)
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.4)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=[job])

    assert scored == []
    assert app.status == "dismissed"
    assert app.match_score == 0.4


@pytest.mark.asyncio
async def test_below_threshold_preserves_applied_status():
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id, status="applied")
    job = _make_job(job_id=job_id)
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.4)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=[job])

    assert scored == []
    assert app.status == "applied"
    assert app.match_score == 0.4


@pytest.mark.asyncio
async def test_above_threshold_stays_pending_review():
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id)
    job = _make_job(job_id=job_id)
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.85)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=[job])

    assert len(scored) == 1
    assert scored[0] is app
    assert app.status == "pending_review"  # unchanged
    assert app.match_score == 0.85


@pytest.mark.asyncio
async def test_mixed_threshold_scores():
    """One above, one at threshold (passes), one below — all get scores persisted."""
    setup_env()
    from app.services.match_service import score_and_match

    ids = [(uuid.uuid4(), uuid.uuid4()) for _ in range(3)]
    apps = [_make_application(app_id=aid, job_id=jid) for aid, jid in ids]
    jobs = [_make_job(job_id=jid) for _, jid in ids]
    scores = [0.9, 0.65, 0.4]

    score_results = [_make_score_result(str(ids[i][0]), scores[i]) for i in range(3)]

    # Session returns a different app for each execute() call
    session = AsyncMock()
    call_count = [0]

    async def execute_side_effect(*args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = apps[call_count[0]]
        call_count[0] += 1
        return result

    session.execute.side_effect = execute_side_effect
    session.commit = AsyncMock()
    session.add = MagicMock()

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": score_results}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(side_effect=apps)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        scored = await score_and_match(profile, session, jobs=jobs)

    assert len(scored) == 2  # 0.9 and 0.65 pass
    assert apps[0].match_score == 0.9
    assert apps[0].status == "pending_review"
    assert apps[1].match_score == 0.65
    assert apps[1].status == "pending_review"
    assert apps[2].match_score == 0.4
    assert apps[2].status == "auto_rejected"


@pytest.mark.asyncio
async def test_per_job_logging_emitted():
    """match.scored is logged once per job with score, passed, and rationale."""
    setup_env()
    from app.services.match_service import score_and_match

    app_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app = _make_application(app_id=app_id, job_id=job_id)
    job = _make_job(job_id=job_id)
    session = _make_mock_session(app)

    score_result = _make_score_result(str(app_id), score=0.75)
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {"scores": [score_result]}

    profile = _make_profile()

    with (
        patch(_GET_SKILLS, new=AsyncMock(return_value=[])),
        patch(_GET_EXPS, new=AsyncMock(return_value=[])),
        patch(_GET_OR_CREATE, new=AsyncMock(return_value=app)),
        patch(_BUILD_GRAPH, return_value=mock_graph),
        patch(_GET_SETTINGS) as mock_get_settings,
    ):
        settings = MagicMock()
        settings.match_score_threshold = 0.65
        mock_get_settings.return_value = settings

        with structlog.testing.capture_logs() as captured:
            await score_and_match(profile, session, jobs=[job])

    scored_events = [e for e in captured if e.get("event") == "match.scored"]
    assert len(scored_events) == 1
    ev = scored_events[0]
    assert ev["application_id"] == str(app_id)
    assert ev["score"] == 0.75
    assert ev["passed"] is True
    assert "rationale" in ev


# ---------------------------------------------------------------------------
# format_profile_text — Locations line is unconditional
# ---------------------------------------------------------------------------

from app.services.match_service import format_profile_text  # noqa: E402


def _profile(target_locations=None, remote_ok=False, full_name=None, seniority=None):
    p = MagicMock()
    p.target_locations = target_locations or []
    p.remote_ok = remote_ok
    p.full_name = full_name
    p.seniority = seniority
    p.target_roles = []
    p.base_resume_md = None
    return p


def test_profile_text_includes_locations_with_cities_and_remote():
    p = _profile(target_locations=["San Francisco", "San Jose"], remote_ok=True)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: San Francisco, San Jose" in text
    assert "Open to remote: yes" in text


def test_profile_text_includes_locations_with_cities_no_remote():
    p = _profile(target_locations=["New York"], remote_ok=False)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: New York" in text
    assert "Open to remote: no" in text


def test_profile_text_remote_only_renders_explicit_none():
    p = _profile(target_locations=[], remote_ok=True)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: (none)" in text
    assert "Open to remote: yes" in text


def test_profile_text_no_remote_no_locations_still_renders():
    """Profile w/ neither cities nor remote still emits the line; LLM never infers."""
    p = _profile(target_locations=[], remote_ok=False)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: (none)" in text
    assert "Open to remote: no" in text


def test_profile_text_handles_none_target_locations():
    """Defensive guard: target_locations=None shouldn't crash; treated as empty list."""
    p = _profile(target_locations=[], remote_ok=False)
    p.target_locations = None  # bypass _profile()'s `or []`
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: (none)" in text
    assert "Open to remote: no" in text
