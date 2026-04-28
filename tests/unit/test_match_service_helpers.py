"""Unit tests for match_service pure helpers."""

import os
import uuid
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")

from app.models.user_profile import Skill, UserProfile, WorkExperience  # noqa: E402
from app.services.match_service import format_profile_text  # noqa: E402


def _make_profile(**overrides) -> UserProfile:
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "full_name": "Alice Engineer",
        "seniority": "senior",
        "target_roles": ["Software Engineer"],
        "remote_ok": True,
    }
    defaults.update(overrides)
    return UserProfile(**defaults)


def test_format_profile_text_renders_core_fields():
    profile = _make_profile()
    text = format_profile_text(profile, skills=[], experiences=[])
    assert "Alice Engineer" in text
    assert "Seniority: senior" in text
    assert "Target roles: Software Engineer" in text
    assert "Open to remote: yes" in text


def test_format_profile_text_groups_skills_by_category():
    profile = _make_profile()
    skills = [
        Skill(profile_id=profile.id, name="Python", category="language"),
        Skill(profile_id=profile.id, name="Go", category="language"),
        Skill(profile_id=profile.id, name="AWS", category="cloud"),
    ]
    text = format_profile_text(profile, skills=skills, experiences=[])
    assert "## Skills" in text
    # Skills under the same category share a line; ordering within category is preserved
    assert "language: Python, Go" in text
    assert "cloud: AWS" in text


def test_format_profile_text_renders_work_experience_with_year_range():
    profile = _make_profile()
    experiences = [
        WorkExperience(
            profile_id=profile.id,
            company="Stripe",
            title="Backend Engineer",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, tzinfo=UTC),
            description_md="Built distributed systems.",
        ),
        WorkExperience(
            profile_id=profile.id,
            company="OpenAI",
            title="Staff Engineer",
            start_date=datetime(2023, 7, 1, tzinfo=UTC),
            end_date=None,
            description_md="Currently shipping LLM pipelines.",
        ),
    ]
    text = format_profile_text(profile, skills=[], experiences=experiences)
    assert "## Work Experience" in text
    assert "Backend Engineer at Stripe (2020–2023)" in text
    # end_date=None ⇒ "present"
    assert "Staff Engineer at OpenAI (2023–present)" in text
    assert "Built distributed systems." in text


def test_format_profile_text_handles_minimal_profile():
    """No name, no skills, no experiences — should still produce a non-crashing output."""
    profile = _make_profile(
        full_name=None,
        seniority=None,
        target_roles=[],
        remote_ok=False,
    )
    text = format_profile_text(profile, skills=[], experiences=[])
    # No mandatory header lines, so output may be empty — but must not raise.
    assert isinstance(text, str)
    # When everything is empty, we should NOT see "Open to remote: yes".
    assert "Open to remote: yes" not in text


def test_format_profile_text_truncates_long_resume():
    """Long base_resume_md is capped at 3000 chars to bound prompt size."""
    profile = _make_profile(base_resume_md="A" * 5000)
    text = format_profile_text(profile, skills=[], experiences=[])
    # The resume is included under "## Resume" and capped.
    resume_section = text.split("## Resume\n", 1)[1]
    assert len(resume_section) <= 3000


def test_format_profile_text_truncates_long_experience_description():
    """Per-experience description_md is capped at 500 chars."""
    profile = _make_profile()
    long_desc = "B" * 1000
    experiences = [
        WorkExperience(
            profile_id=profile.id,
            company="Stripe",
            title="Backend Engineer",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            description_md=long_desc,
        ),
    ]
    text = format_profile_text(profile, skills=[], experiences=experiences)
    assert "B" * 500 in text
    assert "B" * 501 not in text
