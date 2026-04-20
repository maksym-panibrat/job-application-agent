"""
Integration test for the onboarding LangGraph agent with a real PostgreSQL checkpointer.
"""

import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.onboarding import build_graph
from tests.conftest import patch_llm


@pytest.fixture
async def checkpointer(sync_url, asyncpg_url):
    async with AsyncPostgresSaver.from_conn_string(sync_url) as cp:
        await cp.setup()
        yield cp


@pytest.mark.asyncio
async def test_graph_builds_and_invokes(checkpointer):
    with patch_llm("app.agents.onboarding", ["Hello! What roles are you targeting?"]):
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
    assert len(result["messages"]) >= 2
    last = result["messages"][-1]
    assert hasattr(last, "content")
    assert len(last.content) > 0


@pytest.mark.asyncio
async def test_session_resumes_across_invocations(checkpointer):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    with patch_llm("app.agents.onboarding", ["What's your target role?"]):
        graph = build_graph(checkpointer)
        await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "Hello, I'm an engineer."}],
                "profile_id": "test-profile",
                "profile_updates": {},
            },
            config,
        )

    with patch_llm("app.agents.onboarding", ["Got it — targeting Backend Engineer."]):
        graph = build_graph(checkpointer)
        result = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "I want Backend Engineer roles."}],
                "profile_id": "test-profile",
                "profile_updates": {},
            },
            config,
        )

    assert len(result["messages"]) >= 4


@pytest.mark.asyncio
async def test_different_thread_ids_are_isolated(checkpointer):
    thread_a = str(uuid.uuid4())
    thread_b = str(uuid.uuid4())

    with patch_llm("app.agents.onboarding", ["Tell me more."]):
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

    a_msgs = [m.content for m in result_a["messages"] if hasattr(m, "content")]
    b_msgs = [m.content for m in result_b["messages"] if hasattr(m, "content")]

    assert any("Alice" in m for m in a_msgs)
    assert not any("Alice" in m for m in b_msgs)
