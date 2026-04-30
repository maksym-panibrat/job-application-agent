"""Unit tests for app.agents.matching_agent prompt and schema shape."""

from app.agents.matching_agent import (
    SCORING_SYSTEM_PROMPT,
    SCORING_USER_TEMPLATE,
    JobContext,
    ScoreResult,
)


def test_system_prompt_contains_grading_rubric():
    assert "0.9" in SCORING_SYSTEM_PROMPT
    assert "Grading" in SCORING_SYSTEM_PROMPT


def test_system_prompt_contains_location_rule():
    assert "Location" in SCORING_SYSTEM_PROMPT
    # Anti-hedge directive
    assert "Decide" in SCORING_SYSTEM_PROMPT
    assert "may require clarification" in SCORING_SYSTEM_PROMPT


def test_system_prompt_documents_all_output_fields():
    for field in ("summary", "strengths", "gaps", "rationale"):
        assert field in SCORING_SYSTEM_PROMPT


def test_user_template_includes_location_line():
    rendered = SCORING_USER_TEMPLATE.format(
        profile_text="profile",
        title="t",
        company="c",
        location="Berlin",
        workplace_type="hybrid",
        description="d",
    )
    assert "Berlin" in rendered
    assert "hybrid" in rendered
    assert "Location:" in rendered


def test_score_result_has_summary_field():
    sr = ScoreResult(
        application_id="00000000-0000-0000-0000-000000000000",
        score=0.8,
        summary="Senior backend role, Python+AWS, hybrid NYC.",
        rationale="Strong stack fit",
        strengths=["5+ yrs Python"],
        gaps=["Onsite NYC, candidate based in CA"],
    )
    assert sr.summary == "Senior backend role, Python+AWS, hybrid NYC."


def test_job_context_has_location_fields():
    ctx: JobContext = {
        "application_id": "x",
        "title": "t",
        "company": "c",
        "location": "Berlin",
        "workplace_type": "hybrid",
        "description": "d",
    }
    assert ctx["location"] == "Berlin"
    assert ctx["workplace_type"] == "hybrid"
