import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.job import Job
from app.models.user_profile import UserProfile


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
    description: str = "A great job.",
    location: str | None = "Remote",
    workplace_type: str | None = "remote",
    contract_type: str | None = None,
) -> Job:
    return Job(
        id=job_id or uuid.uuid4(),
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title=title,
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        location=location,
        workplace_type=workplace_type,
        description=description,
        contract_type=contract_type,
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


def test_contract_type_hard_rejects_internship_for_senior_profile():
    from app.services.match_service import deterministic_rejection_fields

    profile = _make_profile()
    job = _make_job(
        title="Software Engineering Intern",
        contract_type="internship",
        location="Remote - United States",
    )

    fields = deterministic_rejection_fields(profile, job, 0.65)

    assert fields is not None
    assert fields["policy"] == "contract_type"
    assert fields["score"] < 0.65
    assert "internship" in fields["gaps"][0].lower()


def test_seniority_hard_rejects_new_grad_for_senior_profile():
    from app.services.match_service import deterministic_rejection_fields

    profile = _make_profile()
    job = _make_job(title="New Grad Software Engineer", location="Remote - United States")

    fields = deterministic_rejection_fields(profile, job, 0.65)

    assert fields is not None
    assert fields["policy"] == "seniority"
    assert fields["score"] < 0.65
    assert "new grad" in fields["gaps"][0].lower()


def test_role_family_hard_rejects_sales_role_for_engineering_profile():
    from app.services.match_service import deterministic_rejection_fields

    profile = _make_profile()
    profile.target_roles = ["Senior Backend Engineer", "Platform Engineer"]
    job = _make_job(
        title="Enterprise Account Executive",
        description="Own quota and close enterprise SaaS deals.",
        location="Remote - United States",
    )

    fields = deterministic_rejection_fields(profile, job, 0.65)

    assert fields is not None
    assert fields["policy"] == "role_family"
    assert fields["score"] < 0.65
    assert "outside target role families" in fields["gaps"][0]


def test_candidate_priority_scores_strong_engineering_jobs_above_weak_matches():
    from app.services.match_service import candidate_priority_score

    profile = _make_profile()
    profile.target_roles = ["Senior Backend Engineer"]
    profile.target_locations = ["San Francisco"]
    strong = _make_job(
        title="Senior Backend Engineer",
        description="Build Python APIs, distributed systems, PostgreSQL, and platform services.",
        location="Remote - United States",
        workplace_type="remote",
    )
    weak = _make_job(
        title="Operations Analyst",
        description="Coordinate internal processes and produce spreadsheets.",
        location="Remote - United States",
        workplace_type="remote",
    )

    assert candidate_priority_score(profile, strong) > candidate_priority_score(profile, weak)


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
    p = _profile(target_locations=[], remote_ok=False)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: (none)" in text
    assert "Open to remote: no" in text


def test_profile_text_handles_none_target_locations():
    p = _profile(target_locations=[], remote_ok=False)
    p.target_locations = None
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Target locations: (none)" in text
    assert "Open to remote: no" in text
