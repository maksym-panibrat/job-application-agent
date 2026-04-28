"""
Integration test for the onboarding LangGraph agent with a real PostgreSQL checkpointer.
"""

import uuid
from unittest.mock import patch

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.onboarding import build_graph
from app.agents.test_llm import ToolCapableFakeLLM
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


class _SpyLLM(ToolCapableFakeLLM):
    """Records the system message sent to it on each call."""

    captured_system: list[str] = []  # noqa: RUF012  (class-level capture is intentional in tests)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for m in messages:
            if m.__class__.__name__ == "SystemMessage":
                self.__class__.captured_system.append(str(m.content))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.asyncio
async def test_agent_node_injects_current_profile_snapshot(checkpointer, db_session, asyncpg_url):
    """The system message sent to the LLM must include a snapshot of the current
    profile state (so the LLM can tell what's actually saved vs. what it imagined
    saving — issue #40)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.user import User
    from app.models.user_profile import UserProfile

    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"snap-{user_id}@local")
    db_session.add(user)
    profile = UserProfile(
        user_id=user_id,
        target_roles=["Backend Engineer"],
        target_company_slugs={"greenhouse": ["stripe", "openai"]},
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    profile_id = str(profile.id)

    # Build a fresh session factory bound to the same test database so the
    # agent_node DB lookup uses our seeded data.
    engine = create_async_engine(asyncpg_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    spy = _SpyLLM(responses=["What else should we add?"])
    _SpyLLM.captured_system = []
    with patch("app.agents.onboarding.get_llm", return_value=spy):
        graph = build_graph(checkpointer)
        await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "profile_id": profile_id,
                "profile_updates": {},
            },
            {"configurable": {"thread_id": profile_id, "db_factory": factory}},
        )
    await engine.dispose()

    assert _SpyLLM.captured_system, "Spy LLM never received a system message"
    system_blob = "\n".join(_SpyLLM.captured_system)
    assert "Current Profile" in system_blob
    assert "Backend Engineer" in system_blob
    assert "stripe" in system_blob
    assert "openai" in system_blob


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
