"""Unit tests for generation_agent._extract_text and GenerationOutputError.

_extract_text is the normalization boundary for LLM responses. Silent empty
returns previously corrupted generated documents — these tests pin down the
new behaviour: tool_calls-only / empty responses raise GenerationOutputError.
"""

import os

import pytest
from langchain_core.messages import AIMessage

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")

from app.agents.generation_agent import (  # noqa: E402
    GenerationOutputError,
    _extract_text,
)


def test_extract_text_plain_string_message():
    """AIMessage with plain string content returns that string verbatim."""
    msg = AIMessage(content="Hello, world.")
    assert _extract_text(msg) == "Hello, world."


def test_extract_text_list_of_content_blocks():
    """List of {type: 'text'} blocks is concatenated with newlines."""
    msg = AIMessage(
        content=[
            {"type": "text", "text": "First paragraph."},
            {"type": "text", "text": "Second paragraph."},
        ]
    )
    result = _extract_text(msg)
    assert "First paragraph." in result
    assert "Second paragraph." in result


def test_extract_text_raw_string_accepted_for_backcompat():
    """A raw string (not a message) is still accepted."""
    assert _extract_text("just a string") == "just a string"


def test_extract_text_raises_on_tool_calls_only():
    """Message with tool_calls and empty content raises, mentioning tool_calls count."""
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "some_tool", "args": {"foo": "bar"}, "id": "call-0"},
            {"name": "another_tool", "args": {}, "id": "call-1"},
        ],
    )
    with pytest.raises(GenerationOutputError, match=r"tool_calls \(2\)"):
        _extract_text(msg)


def test_extract_text_raises_on_empty_string_no_tool_calls():
    """Message with empty content and no tool_calls raises 'empty text content'."""
    msg = AIMessage(content="")
    with pytest.raises(GenerationOutputError, match="empty text content"):
        _extract_text(msg)


def test_extract_text_raises_on_whitespace_only():
    """Whitespace-only content is treated as empty."""
    msg = AIMessage(content="   \n  \t  ")
    with pytest.raises(GenerationOutputError, match="empty text content"):
        _extract_text(msg)


def test_extract_text_raises_on_empty_block_list():
    """A list with no text blocks collapses to empty and raises."""
    msg = AIMessage(content=[{"type": "tool_use", "id": "x", "name": "t", "input": {}}])
    with pytest.raises(GenerationOutputError, match="empty text content"):
        _extract_text(msg)
