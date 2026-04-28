"""Unit tests for the generation LangGraph agent (cover-letter only, sync)."""

import os
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")

from app.agents.generation_agent import build_graph  # noqa: E402
from tests.conftest import patch_llm  # noqa: E402

_BASE_STATE = {
    "application_id": str(uuid.uuid4()),
    "profile_text": "Senior Python engineer with FastAPI and asyncio experience.",
    "job_title": "Python Engineer",
    "job_company": "Acme Corp",
    "job_description": "Backend role requiring Python, FastAPI, and PostgreSQL.",
    "base_resume_md": "# Alice\n\n5 years of Python engineering experience.",
    "document": None,
}


@pytest.mark.asyncio
async def test_graph_generates_cover_letter_synchronously():
    """Graph produces a single cover_letter doc and reaches END."""
    with patch_llm(
        "app.agents.generation_agent",
        ["Dear Hiring Team,\n\nI bring strong Python and FastAPI experience to this role."],
    ):
        graph = build_graph()
        result = await graph.ainvoke(_BASE_STATE)

    doc = result["document"]
    assert doc is not None
    assert doc["doc_type"] == "cover_letter"
    assert "Hiring Team" in doc["content_md"]
    assert doc["generation_model"]


@pytest.mark.asyncio
async def test_document_has_required_fields():
    """The generated document carries doc_type, content_md, generation_model."""
    with patch_llm(
        "app.agents.generation_agent",
        ["A solid cover letter with enough words to clear the length floor."],
    ):
        graph = build_graph()
        result = await graph.ainvoke(_BASE_STATE)

    doc = result["document"]
    assert doc["doc_type"] == "cover_letter"
    assert len(doc["content_md"]) > 30
    assert doc["generation_model"]


def test_cover_letter_prompt_targets_concise_length():
    """Prompt instructs the LLM to write a concise letter (≤140 words),
    not the previous 250–350 word default that produced unreadable output."""
    from app.agents.generation_agent import COVER_LETTER_PROMPT

    # Stale 250-350 word target must be gone
    assert "250" not in COVER_LETTER_PROMPT
    assert "350" not in COVER_LETTER_PROMPT
    # New concise target present
    assert "140" in COVER_LETTER_PROMPT
