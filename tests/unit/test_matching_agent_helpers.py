"""Unit tests for matching_agent pure helpers."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")

from app.agents.matching_agent import MAX_JOB_DESC_CHARS, truncate_description  # noqa: E402


def test_truncate_description_passthrough_under_limit():
    """Inputs at or below the cap are returned unchanged."""
    short = "x" * (MAX_JOB_DESC_CHARS - 1)
    assert truncate_description(short) == short

    exactly = "x" * MAX_JOB_DESC_CHARS
    assert truncate_description(exactly) == exactly


def test_truncate_description_appends_marker_when_over_limit():
    """Inputs over the cap are truncated and end with the [Description truncated] marker."""
    long = "x" * (MAX_JOB_DESC_CHARS + 100)
    result = truncate_description(long)
    assert result.startswith("x" * MAX_JOB_DESC_CHARS)
    assert "[Description truncated]" in result
    assert "x" * (MAX_JOB_DESC_CHARS + 1) not in result


def test_truncate_description_handles_empty_and_none():
    """Falsy inputs return an empty string instead of None or the marker."""
    assert truncate_description("") == ""
    assert truncate_description(None) == ""  # type: ignore[arg-type]


def test_truncate_description_respects_explicit_limit():
    """The optional max_chars parameter overrides the default cap."""
    text = "abcdefghij"
    result = truncate_description(text, max_chars=5)
    assert result.startswith("abcde")
    assert "[Description truncated]" in result
