"""
Unit tests for the generation LangGraph agent.

Uses MemorySaver (in-memory checkpointer) and ToolCapableFakeLLM to avoid
real DB or LLM calls. `save_documents_node` is patched at the module boundary.
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.agents.generation_agent import build_graph  # noqa: E402
from tests.conftest import patch_llm  # noqa: E402

_BASE_STATE = {
    "application_id": str(uuid.uuid4()),
    "profile_text": "Senior Python engineer with FastAPI and asyncio experience.",
    "job_title": "Python Engineer",
    "job_company": "Acme Corp",
    "job_description": "Backend role requiring Python, FastAPI, and PostgreSQL.",
    "base_resume_md": "# Alice\n\n5 years of Python engineering experience.",
    "custom_questions": [],
    "documents": [],
    "generation_status": "none",
    "user_decision": {},
}


def _mock_session_ctx():
    """Async context manager mock for get_session_factory() return value."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_cm)


def _graph_patches():
    """Stack the two patches needed to isolate save_documents_node from a real DB."""
    p1 = patch("app.database.get_session_factory", return_value=_mock_session_ctx())
    p2 = patch("app.services.application_service.save_documents", new=AsyncMock())
    return p1, p2


@pytest.mark.asyncio
async def test_graph_generates_two_docs_without_custom_questions():
    """Graph produces tailored_resume + cover_letter, pauses before review."""
    p1, p2 = _graph_patches()
    with patch_llm("app.agents.generation_agent", [
        "# Tailored Resume\n\nStrong Python background relevant to this role.",
        "Dear Hiring Manager,\n\nI am excited to apply for this position.",
    ]):
        with p1, p2:
            checkpointer = MemorySaver()
            graph = build_graph(checkpointer)
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            result = await graph.ainvoke(_BASE_STATE, config)

    assert len(result["documents"]) == 2
    doc_types = {d["doc_type"] for d in result["documents"]}
    assert "tailored_resume" in doc_types
    assert "cover_letter" in doc_types
    # Graph paused before review — generation_status still "generating"
    assert result["generation_status"] == "generating"


@pytest.mark.asyncio
async def test_graph_generates_three_docs_with_custom_questions():
    """Graph adds custom_answers document when custom_questions are provided."""
    state = {
        **_BASE_STATE,
        "custom_questions": ["Describe a challenging project you led."],
    }
    p1, p2 = _graph_patches()
    with patch_llm("app.agents.generation_agent", [
        "# Tailored Resume",
        "Dear Hiring Manager",
        "**Q: Describe a challenging project**\nA: I led a microservices migration.",
    ]):
        with p1, p2:
            checkpointer = MemorySaver()
            graph = build_graph(checkpointer)
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            result = await graph.ainvoke(state, config)

    assert len(result["documents"]) == 3
    doc_types = {d["doc_type"] for d in result["documents"]}
    assert "custom_answers" in doc_types


@pytest.mark.asyncio
async def test_graph_resumes_and_finalizes_on_approval():
    """
    Full 3-step interrupt flow:
    1. First ainvoke: parallel generation, pauses before review (interrupt_before)
    2. Second ainvoke(None): enters review_node, calls interrupt({...}), pauses again
    3. Third ainvoke(Command(resume=...)): provides decision, graph finalizes
    """
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    p1, p2 = _graph_patches()

    with patch_llm("app.agents.generation_agent", ["# Resume", "Cover letter"]):
        with p1, p2:
            checkpointer = MemorySaver()
            graph = build_graph(checkpointer)

            # Step 1: generate docs, pause before review
            await graph.ainvoke(_BASE_STATE, config)

            # Step 2: enter review_node, hit interrupt()
            await graph.ainvoke(None, config)

            # Step 3: provide user decision, finalize
            result = await graph.ainvoke(Command(resume={"approved": True}), config)

    assert result["generation_status"] == "ready"


@pytest.mark.asyncio
async def test_documents_have_required_fields():
    """Each generated document has doc_type, content_md, and generation_model."""
    p1, p2 = _graph_patches()
    with patch_llm("app.agents.generation_agent", ["# Resume content", "Cover letter content"]):
        with p1, p2:
            checkpointer = MemorySaver()
            graph = build_graph(checkpointer)
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            result = await graph.ainvoke(_BASE_STATE, config)

    for doc in result["documents"]:
        assert "doc_type" in doc
        assert "content_md" in doc
        assert "generation_model" in doc
        assert len(doc["content_md"]) > 0
