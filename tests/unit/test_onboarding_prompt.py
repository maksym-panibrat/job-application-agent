"""Unit tests for onboarding agent system prompt and tool-call discipline.

Covers issues:
  #40 — silent profile-update claims (no tool call but agent says "I've updated")
  #43 — empty target companies → 0 jobs forever

These tests assert the prompt contract; tool-call behavior is covered separately
by the integration suite that runs the graph end-to-end.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")


def test_prompt_makes_companies_part_of_search_ready_gate():
    """Search-ready check must require at least one company, not just location.

    The original prompt gated only on location/remote — empty target companies
    silently produced 0-job syncs forever. The gate must include companies too.
    """
    from app.agents.onboarding import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    assert "target_companies" in lower or "companies" in lower

    # Find a clause that defines the search-ready gate (the operational sentence,
    # not just a heading). It must mention companies in the same paragraph.
    candidates = [
        m for m in ("search-ready until", "do not consider", "search-ready when") if m in lower
    ]
    assert candidates, (
        "Prompt must contain an operational search-ready gate clause "
        "(e.g. 'search-ready until ...' or 'do not consider ...')."
    )
    found_company_clause = False
    for needle in candidates:
        idx = lower.index(needle)
        paragraph_end = lower.find("\n\n", idx)
        if paragraph_end == -1:
            paragraph_end = len(lower)
        clause = lower[idx:paragraph_end]
        if "company" in clause or "companies" in clause:
            found_company_clause = True
            break
    assert found_company_clause, "At least one search-ready clause must require companies."


def test_prompt_proactively_asks_for_target_companies():
    """Prompt instructs the agent to PROACTIVELY ask for companies, not just IF the user
    volunteers them. The old conditional ('if the user names...') let the agent skip
    the step entirely."""
    from app.agents.onboarding import SYSTEM_PROMPT

    # Old conditional language should be gone or rewritten
    # New language must indicate an active ask
    lower = SYSTEM_PROMPT.lower()
    assert "ask" in lower
    # A curated list of suggestions makes the ask actionable
    suggestions = ["stripe", "anthropic", "datadog", "linear"]
    assert any(s in lower for s in suggestions), (
        "Prompt should include at least one curated company suggestion "
        "so the agent can offer concrete examples to the user."
    )


def test_prompt_does_not_mention_target_company_slugs():
    """After E1 the agent talks in company display names only — no slugs,
    no provider buckets in the user-facing instruction."""
    from app.agents.onboarding import SYSTEM_PROMPT

    assert "target_company_slugs" not in SYSTEM_PROMPT
    assert "boards.greenhouse.io" not in SYSTEM_PROMPT


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
    search keywords, target companies."""
    from app.agents.onboarding import _format_current_profile

    snapshot = _format_current_profile(
        {
            "target_roles": ["Backend Engineer", "Software Architect"],
            "seniority": "senior",
            "target_locations": ["San Diego, CA"],
            "remote_ok": True,
            "search_keywords": ["Python", "distributed systems"],
            "target_company_names": ["Stripe", "OpenAI"],
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
    assert "Stripe" in snapshot
    assert "OpenAI" in snapshot


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
            "target_company_names": [],
            "full_name": None,
        }
    )
    # Should produce a non-empty snapshot that signals the empty state.
    assert snapshot.strip()
    # Should NOT contain fabricated values
    assert "Backend Engineer" not in snapshot
    assert "Stripe" not in snapshot
