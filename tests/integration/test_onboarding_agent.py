"""
Integration test for the onboarding LangGraph agent with a real PostgreSQL checkpointer.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import ToolNode

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

    from app.models.company import Company
    from app.models.user import User
    from app.models.user_profile import UserProfile

    # Seed two companies that the profile will follow.
    stripe = Company(
        canonical_name="Stripe",
        normalized_key="stripe",
        provider_slugs={"greenhouse": "stripe"},
        resolved_at=datetime.now(UTC),
    )
    openai = Company(
        canonical_name="OpenAI",
        normalized_key="openai",
        provider_slugs={"greenhouse": "openai"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([stripe, openai])
    await db_session.commit()
    await db_session.refresh(stripe)
    await db_session.refresh(openai)

    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"snap-{user_id}@local")
    db_session.add(user)
    profile = UserProfile(
        user_id=user_id,
        target_roles=["Backend Engineer"],
        target_company_ids=[stripe.id, openai.id],
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
    assert "Stripe" in system_blob
    assert "OpenAI" in system_blob


async def _make_profile(db_session):
    """Create a real User + UserProfile pair, satisfying the FK constraint."""
    from app.models.user import User
    from app.models.user_profile import UserProfile

    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"company-test-{user_id}@local")
    db_session.add(user)
    profile = UserProfile(user_id=user_id)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_persist_inferred_companies_resolves_and_appends_ids(db_session, monkeypatch):
    """persist_inferred_companies must route every name through company_resolver
    and append the resulting Company.id to profile.target_company_ids. Names
    that fail to resolve are skipped (logged) rather than exploding."""
    from app.models.company import Company
    from app.services import company_resolver

    stripe = Company(
        canonical_name="Stripe",
        normalized_key="stripe",
        provider_slugs={"greenhouse": "stripe"},
        resolved_at=datetime.now(UTC),
    )
    airbnb = Company(
        canonical_name="Airbnb",
        normalized_key="airbnb",
        provider_slugs={"greenhouse": "airbnb"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([stripe, airbnb])
    await db_session.commit()
    await db_session.refresh(stripe)
    await db_session.refresh(airbnb)

    seen: list[str] = []

    async def fake_resolve(name, session):
        seen.append(name)
        if name.lower() == "stripe":
            return stripe
        if name.lower() == "airbnb":
            return airbnb
        return None  # "openai" → unresolved, must be skipped

    monkeypatch.setattr(company_resolver, "resolve", fake_resolve)

    from app.agents.onboarding import persist_inferred_companies

    profile = await _make_profile(db_session)
    resolved = await persist_inferred_companies(profile, ["Airbnb", "OpenAI", "Stripe"], db_session)
    await db_session.refresh(profile)

    assert sorted(resolved) == ["Airbnb", "Stripe"]
    assert sorted(profile.target_company_ids) == sorted([airbnb.id, stripe.id])
    # OpenAI was attempted but did not resolve.
    assert "OpenAI" in seen


@pytest.mark.asyncio
async def test_persist_inferred_companies_respects_free_limit(db_session, monkeypatch):
    from app.agents.onboarding import persist_inferred_companies
    from app.models.company import Company
    from app.models.user import User
    from app.models.user_profile import UserProfile
    from app.services import company_resolver
    from app.services.entitlements import CompanyFollowLimitError

    existing = [
        Company(
            canonical_name=f"Existing {i}",
            normalized_key=f"existing-{uuid.uuid4()}",
            provider_slugs={"greenhouse": f"existing-{uuid.uuid4()}"},
            resolved_at=datetime.now(UTC),
        )
        for i in range(5)
    ]
    overflow = Company(
        canonical_name="Overflow",
        normalized_key=f"overflow-{uuid.uuid4()}",
        provider_slugs={"greenhouse": f"overflow-{uuid.uuid4()}"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([*existing, overflow])
    await db_session.commit()
    for company in [*existing, overflow]:
        await db_session.refresh(company)

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"free-limit-{user_id}@local",
        subscription_plan="free",
        subscription_status="inactive",
    )
    profile = UserProfile(
        user_id=user_id,
        target_company_ids=[company.id for company in existing],
    )
    db_session.add(user)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    existing_ids = [company.id for company in existing]

    async def fake_resolve(name, session):
        if name == "Overflow":
            return overflow
        return None

    monkeypatch.setattr(company_resolver, "resolve", fake_resolve)

    with pytest.raises(CompanyFollowLimitError):
        await persist_inferred_companies(profile, ["Overflow"], db_session)

    db_session.expire_all()
    await db_session.refresh(profile)
    assert profile.target_company_ids == existing_ids


@pytest.mark.asyncio
async def test_company_limit_error_is_visible_to_onboarding_agent(
    checkpointer, db_session, asyncpg_url, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.agents.onboarding import build_graph
    from app.models.company import Company
    from app.models.user import User
    from app.models.user_profile import UserProfile
    from app.services import company_resolver

    existing = [
        Company(
            canonical_name=f"Existing {i}",
            normalized_key=f"existing-visible-{uuid.uuid4()}",
            provider_slugs={"greenhouse": f"existing-visible-{uuid.uuid4()}"},
            resolved_at=datetime.now(UTC),
        )
        for i in range(5)
    ]
    overflow = Company(
        canonical_name="Overflow",
        normalized_key=f"overflow-visible-{uuid.uuid4()}",
        provider_slugs={"greenhouse": f"overflow-visible-{uuid.uuid4()}"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([*existing, overflow])
    await db_session.commit()
    for company in [*existing, overflow]:
        await db_session.refresh(company)

    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"visible-limit-{user_id}@local")
    profile = UserProfile(
        user_id=user_id,
        target_company_ids=[company.id for company in existing],
    )
    db_session.add(user)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    async def fake_resolve(name, session):
        if name == "Overflow":
            return overflow
        return None

    monkeypatch.setattr(company_resolver, "resolve", fake_resolve)

    engine = create_async_engine(asyncpg_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    spy = _SpyLLM(
        responses=[
            '{"updates": "{\\"target_companies\\": [\\"Overflow\\"]}"}',
            "I could not save that company.",
        ]
    )
    _SpyLLM.captured_system = []
    with patch("app.agents.onboarding.get_llm", return_value=spy):
        graph = build_graph(checkpointer)
        await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": "Add Overflow"}],
                "profile_id": str(profile.id),
                "profile_updates": {},
            },
            {"configurable": {"thread_id": str(profile.id), "db_factory": factory}},
        )
    await engine.dispose()

    system_blob = "\n".join(_SpyLLM.captured_system)
    assert "Persistence Errors" in system_blob
    assert "Free accounts can follow up to 5 companies." in system_blob


@pytest.mark.asyncio
async def test_persist_inferred_companies_handles_fanout_timeout(db_session, monkeypatch):
    """A FanoutTimeoutError on one name must not abort the whole batch — the
    name is logged and skipped, the rest still resolve."""
    from app.models.company import Company
    from app.services import company_resolver

    stripe = Company(
        canonical_name="Stripe",
        normalized_key="stripe",
        provider_slugs={"greenhouse": "stripe"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(stripe)
    await db_session.commit()
    await db_session.refresh(stripe)

    async def fake_resolve(name, session):
        if name.lower() == "flaky":
            raise company_resolver.FanoutTimeoutError(name)
        if name.lower() == "stripe":
            return stripe
        return None

    monkeypatch.setattr(company_resolver, "resolve", fake_resolve)

    from app.agents.onboarding import persist_inferred_companies

    profile = await _make_profile(db_session)
    resolved = await persist_inferred_companies(profile, ["Flaky", "Stripe"], db_session)
    await db_session.refresh(profile)

    assert resolved == ["Stripe"]
    assert profile.target_company_ids == [stripe.id]


def test_list_curated_companies_is_registered_with_tool_node():
    """Regression guard: list_curated_companies must be wired into the
    ToolNode so the LLM can actually dispatch it. Invoking the tool
    directly via .ainvoke() (in test_onboarding_list_catalog_tool.py)
    does not prove this — a refactor that drops the tool from the
    `tools = [...]` list in build_graph would silently break Layer 3
    chat-driven company suggestions with no test failure.

    Uses an in-memory checkpointer so this test stays fast and DB-free."""
    graph = build_graph(MemorySaver())
    tool_node_wrapper = graph.nodes["tools"]
    # LangGraph wraps user-supplied nodes in a Pregel `PregelNode`; the
    # original ToolNode lives on `.bound`.
    tool_node = tool_node_wrapper.bound
    assert isinstance(tool_node, ToolNode), f"Expected ToolNode, got {type(tool_node).__name__}"
    registered = set(tool_node.tools_by_name.keys())
    assert "list_curated_companies" in registered, (
        f"list_curated_companies missing from ToolNode; registered tools: {registered}"
    )
    # Sanity: save_profile_updates is the other half of the contract.
    assert "save_profile_updates" in registered


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
