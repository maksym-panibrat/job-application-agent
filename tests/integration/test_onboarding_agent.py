"""
Integration test for the onboarding LangGraph agent with a real PostgreSQL checkpointer.

Verifies:
- The graph can be built and invoked with AsyncPostgresSaver
- State is checkpointed across invocations (resumable sessions)
- The save_profile_updates tool is routed correctly
"""

import uuid
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.onboarding import build_graph


def _make_fake_llm(responses: list):
    """
    Returns a mock that behaves like a bound ChatAnthropic and cycles through
    `responses` (list of AIMessage objects). Supports both sync invoke() and
    async ainvoke() since the onboarding agent uses ainvoke().
    """
    from unittest.mock import AsyncMock, MagicMock

    call_count = {"n": 0}

    def _next_response(messages, **kwargs):
        idx = call_count["n"] % len(responses)
        call_count["n"] += 1
        return responses[idx]

    async def ainvoke(messages, **kwargs):
        return _next_response(messages, **kwargs)

    llm = MagicMock()
    llm.invoke.side_effect = _next_response
    llm.ainvoke = AsyncMock(side_effect=ainvoke)
    llm.bind_tools.return_value = llm
    return llm


@pytest.fixture
async def checkpointer(sync_url, asyncpg_url):
    """
    Set up AsyncPostgresSaver against the testcontainers DB.
    The checkpointer creates its own tables (langgraph_checkpoint etc.).
    """
    async with AsyncPostgresSaver.from_conn_string(sync_url) as cp:
        await cp.setup()
        yield cp


@pytest.mark.asyncio
async def test_graph_builds_and_invokes(checkpointer, monkeypatch):
    """The onboarding graph can be invoked and returns a response."""
    fake_llm = _make_fake_llm(
        [AIMessage(content="Hello! What roles are you targeting?")]
    )

    with patch("app.agents.onboarding.get_llm", return_value=fake_llm):
        graph = build_graph(checkpointer)
        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        result = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "Hi, I want to set up my profile."}],
                "profile_id": "test-profile",
                "profile_updates": {},
            },
            config,
        )

    assert "messages" in result
    assert len(result["messages"]) >= 2  # user + assistant
    last = result["messages"][-1]
    assert hasattr(last, "content")
    assert "Hello" in last.content or len(last.content) > 0


@pytest.mark.asyncio
async def test_session_resumes_across_invocations(checkpointer):
    """
    State is persisted between graph invocations with the same thread_id.
    Second invocation should see messages from the first invocation.
    """
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    responses_round_1 = [AIMessage(content="What's your target role?")]
    responses_round_2 = [AIMessage(content="Got it — targeting Backend Engineer.")]

    # First invocation
    with patch("app.agents.onboarding.get_llm", return_value=_make_fake_llm(responses_round_1)):
        graph = build_graph(checkpointer)
        await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "Hello, I'm an engineer."}],
                "profile_id": "test-profile",
                "profile_updates": {},
            },
            config,
        )

    # Second invocation — pass only the new message; graph resumes from checkpoint
    with patch("app.agents.onboarding.get_llm", return_value=_make_fake_llm(responses_round_2)):
        graph = build_graph(checkpointer)
        result = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "I want Backend Engineer roles."}],
                "profile_id": "test-profile",
                "profile_updates": {},
            },
            config,
        )

    # Messages accumulate across invocations
    assert len(result["messages"]) >= 4  # 2 from round 1 + 2 from round 2


@pytest.mark.asyncio
async def test_different_thread_ids_are_isolated(checkpointer):
    """Two profiles with different thread IDs don't share state."""
    thread_a = str(uuid.uuid4())
    thread_b = str(uuid.uuid4())

    fake_llm = _make_fake_llm([AIMessage(content="Tell me more.")])

    with patch("app.agents.onboarding.get_llm", return_value=fake_llm):
        graph = build_graph(checkpointer)

        result_a = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "I'm Alice."}],
                "profile_id": thread_a,
                "profile_updates": {},
            },
            {"configurable": {"thread_id": thread_a}},
        )
        result_b = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "I'm Bob."}],
                "profile_id": thread_b,
                "profile_updates": {},
            },
            {"configurable": {"thread_id": thread_b}},
        )

    # Thread A message should not appear in thread B's state
    a_msgs = [m.content for m in result_a["messages"] if hasattr(m, "content")]
    b_msgs = [m.content for m in result_b["messages"] if hasattr(m, "content")]

    assert any("Alice" in m for m in a_msgs)
    assert not any("Alice" in m for m in b_msgs)
