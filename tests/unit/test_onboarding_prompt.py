"""Unit tests for onboarding agent system prompt and tool-call discipline.

Covers issues:
  #40 — silent profile-update claims (no tool call but agent says "I've updated")
  #43 — empty target_company_slugs.greenhouse → 0 jobs forever

These tests assert the prompt contract; tool-call behavior is covered separately
by the integration suite that runs the graph end-to-end.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")


def test_prompt_mentions_both_search_ready_requirements():
    """Search-ready is gated on (location OR remote_ok) AND target_company_slugs.

    Issue #43: empty greenhouse slug list silently produced 0-job syncs because
    the original prompt only gated on location. Asserts the prompt names both
    fields somewhere — phrasing can vary, but both must appear so the LLM has
    something to gate on. (Behavioral verification of the actual gate happens
    in the integration suite.)
    """
    from app.agents.onboarding import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    location_mentioned = "target_locations" in lower or "remote_ok" in lower
    slug_mentioned = "target_company_slugs" in lower or "greenhouse" in lower
    assert location_mentioned, "Prompt must reference target_locations / remote_ok"
    assert slug_mentioned, "Prompt must reference target_company_slugs / greenhouse boards"


def test_prompt_proactively_asks_for_target_companies():
    """Prompt instructs the agent to PROACTIVELY ask for slugs, not just IF the user
    volunteers them. The old conditional ('if the user names...') let the agent skip
    the step entirely."""
    from app.agents.onboarding import SYSTEM_PROMPT

    # Old conditional language should be gone or rewritten
    # New language must indicate an active ask
    lower = SYSTEM_PROMPT.lower()
    assert "ask" in lower
    # A curated list of suggestions makes the ask actionable
    suggestions = ["stripe", "openai", "anthropic", "datadog"]
    assert any(s in lower for s in suggestions), (
        "Prompt should include at least one curated Greenhouse slug suggestion "
        "so the agent can offer concrete examples to the user."
    )


def test_prompt_mandates_tool_call_before_claiming_save():
    """Prompt forbids the agent from saying 'I've updated/saved/adjusted' without
    actually invoking save_profile_updates in the same turn (the #40 bug)."""
    from app.agents.onboarding import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    # Must contain a mandatory directive about tool usage (MUST / always / never).
    assert "must call" in lower or "must invoke" in lower or "always call" in lower
    # Must explicitly forbid fabricated save claims
    assert (
        "never claim" in lower
        or "do not claim" in lower
        or "do not say" in lower
        or "never say" in lower
    )


def test_format_current_profile_includes_relevant_fields():
    """The helper that injects the current profile snapshot must surface the
    fields the LLM needs as ground truth: roles, seniority, location, remote_ok,
    search keywords, target company slugs."""
    from app.agents.onboarding import _format_current_profile

    snapshot = _format_current_profile(
        {
            "target_roles": ["Backend Engineer", "Software Architect"],
            "seniority": "senior",
            "target_locations": ["San Diego, CA"],
            "remote_ok": True,
            "search_keywords": ["Python", "distributed systems"],
            "target_company_slugs": {"greenhouse": ["stripe", "openai"]},
            "full_name": "Maksym P.",
        }
    )

    # Each ground-truth field must be visible to the LLM.
    assert "Backend Engineer" in snapshot
    assert "Software Architect" in snapshot
    assert "senior" in snapshot.lower()
    assert "San Diego" in snapshot
    assert "remote_ok" in snapshot.lower() or "remote" in snapshot.lower()
    assert "Python" in snapshot
    assert "stripe" in snapshot
    assert "openai" in snapshot


def test_format_current_profile_handles_missing_fields():
    """Empty/None values must not crash and must not invent values."""
    from app.agents.onboarding import _format_current_profile

    snapshot = _format_current_profile(
        {
            "target_roles": [],
            "seniority": None,
            "target_locations": [],
            "remote_ok": False,
            "search_keywords": [],
            "target_company_slugs": {},
            "full_name": None,
        }
    )
    # Should produce a non-empty snapshot that signals the empty state.
    assert snapshot.strip()
    # Should NOT contain fabricated values
    assert "Backend Engineer" not in snapshot
    assert "stripe" not in snapshot
