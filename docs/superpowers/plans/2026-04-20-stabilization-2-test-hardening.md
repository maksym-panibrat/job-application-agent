# Stabilization Spec 2 — Test Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock Spec 1 fixes into regressions, migrate MagicMock LLM fakes to `ToolCapableFakeLLM`, backfill coverage on generation_agent, rate_limit_service, auth JWT path, and application lifecycle, add frontend component tests with MSW, and enforce coverage thresholds in CI.

**Architecture:** Shared `patch_llm()` helper in `tests/conftest.py` replaces per-file `MagicMock` LLM fakes. `ToolCapableFakeLLM` (in `app/agents/test_llm.py`) is extended to auto-populate `tool_calls` from JSON response strings so matching-agent tool-call structure is exercised. Frontend tests use MSW v2 for network-layer mocking.

**Tech Stack:** pytest, pytest-cov, testcontainers, `FakeListChatModel`, `MemorySaver`, MSW v2, `@vitest/coverage-v8`, React Testing Library, `@testing-library/user-event`

---

## File map

| File | Action | What it does |
|------|--------|--------------|
| `app/agents/test_llm.py` | Modify | Extend `ToolCapableFakeLLM` to produce tool_calls from JSON responses |
| `tests/conftest.py` | Create | `patch_llm(module_path, responses)` shared helper |
| `pyproject.toml` | Modify | Add `pytest-cov>=5.0` to dev deps |
| `tests/integration/test_onboarding_agent.py` | Modify | Swap local `_make_fake_llm` for `patch_llm` |
| `tests/integration/test_match_scoring.py` | Modify | Swap local `_make_llm_mock` for `patch_llm` |
| `tests/unit/test_match_service.py` | Modify | Replace `_make_profile/job/application` MagicMocks with real SQLModel instances |
| `tests/unit/test_generation_agent.py` | Create | Tests for generation agent nodes and interrupt/resume flow |
| `tests/integration/test_rate_limit_service.py` | Create | Tests for sliding-window rate limiter and daily quota |
| `tests/integration/test_auth_oauth.py` | Create | Tests for JWT auth: valid token accepted, invalid/expired rejected |
| `tests/integration/test_application_service_lifecycle.py` | Create | Tests for `save_documents` upsert and `generate_materials` state transitions |
| `frontend/src/test/handlers.ts` | Create | Default MSW request handlers |
| `frontend/src/test/server.ts` | Create | MSW Node server for Vitest |
| `frontend/src/test/setup.ts` | Modify | Wire MSW server lifecycle |
| `frontend/src/components/BudgetBanner.test.tsx` | Create | Tests for budget banner visibility and date formatting |
| `frontend/src/test/MatchCard.test.tsx` | Delete | Mislabeled (tests Matches page, not MatchCard) |
| `frontend/src/components/MatchCard.test.tsx` | Create | Tests for MatchCard renders, interest buttons, dismiss |
| `frontend/src/context/AuthContext.test.tsx` | Create | Tests for token hydration, signOut, getMe failure |
| `frontend/src/api/client.test.ts` | Create | Tests for apiFetch error handling and SSE streaming |
| `frontend/vite.config.ts` | Modify | Add v8 coverage config |
| `frontend/package.json` | Modify | Add msw, @vitest/coverage-v8 |
| `.github/workflows/ci.yml` | Modify | Add --cov-fail-under flags after baseline measured |
| `.pre-commit-config.yaml` | Create | ruff + unit-test pre-push hook |

---

## Task 1: Extend ToolCapableFakeLLM for tool-call support

**Files:**
- Modify: `app/agents/test_llm.py`

**Background:** The matching agent calls `llm.bind_tools([record_score])` then reads `result.tool_calls[0]["args"]`. The existing `ToolCapableFakeLLM` ignores `bind_tools` and returns plain `AIMessage` with no `tool_calls`. This task extends it so JSON response strings are automatically promoted to tool calls when tools are bound.

- [ ] **Step 1: Read the current file**

Run: `cat app/agents/test_llm.py`

Confirm it shows `ToolCapableFakeLLM` with a `bind_tools` that returns `self` and no tool_calls logic.

- [ ] **Step 2: Replace `app/agents/test_llm.py` with the extended version**

```python
import json
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

_DEFAULT_RESPONSES: dict[str, list[str]] = {
    "onboarding": [
        "I've saved your profile! Is there anything else you'd like to update?",
        '{"target_title": "Software Engineer", "location": "Remote"}',
    ],
    "matching": [
        '{"score": 0.75, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}',
    ],
    "generation": [
        "Tailored resume content here.",
        "Tailored cover letter content here.",
    ],
    "resume_extraction": [
        '{"name": "Test User", "skills": ["Python"], "work_experience": []}',
    ],
}


class ToolCapableFakeLLM(FakeListChatModel):
    """
    FakeListChatModel that:
    - Accepts bind_tools() without raising NotImplementedError
    - Auto-populates tool_calls on the returned AIMessage when tools are bound
      and the response string parses as a valid JSON dict.
    """

    _bound_tool_name: str | None = PrivateAttr(default=None)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeLLM":
        if tools:
            first = tools[0]
            self._bound_tool_name = getattr(first, "name", None) or getattr(
                first, "__name__", str(first)
            )
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if not self._bound_tool_name:
            return result
        new_gens = []
        for gen in result.generations:
            content = gen.message.content
            try:
                args = json.loads(content)
                if isinstance(args, dict):
                    new_gens.append(
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": self._bound_tool_name,
                                        "args": args,
                                        "id": "fake-tool-call-0",
                                    }
                                ],
                            )
                        )
                    )
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            new_gens.append(gen)
        return ChatResult(generations=new_gens)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def get_fake_llm(purpose: str = "matching") -> ToolCapableFakeLLM:
    responses = _DEFAULT_RESPONSES.get(purpose, ["fake response"])
    return ToolCapableFakeLLM(responses=responses)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `uv run pytest tests/unit/ tests/integration/ -q`

Expected: `103 passed` (or higher). Zero failures.

- [ ] **Step 4: Commit**

```bash
git add app/agents/test_llm.py
git commit -m "fix(test_llm): extend ToolCapableFakeLLM to produce tool_calls from JSON responses"
```

---

## Task 2: Shared LLM fixture + pytest-cov

**Files:**
- Create: `tests/conftest.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
from unittest.mock import patch

from app.agents.test_llm import ToolCapableFakeLLM


def patch_llm(module_path: str, responses: list[str]):
    """
    Return a unittest.mock.patch context manager that replaces get_llm() at
    `module_path` with ToolCapableFakeLLM(responses=responses).

    Usage:
        with patch_llm("app.agents.onboarding", ["Hello!"]):
            result = await graph.ainvoke(...)
    """
    fake = ToolCapableFakeLLM(responses=responses)
    return patch(f"{module_path}.get_llm", return_value=fake)
```

- [ ] **Step 2: Verify the fixture is importable**

Run: `python -c "from tests.conftest import patch_llm; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Add pytest-cov to pyproject.toml**

In `pyproject.toml`, change the `[dependency-groups] dev` section to add `"pytest-cov>=5.0",`:

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "anyio>=4.0",
    "respx>=0.21",
    "testcontainers[postgres]>=4.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "pytest-cov>=5.0",
]
```

- [ ] **Step 4: Install updated deps**

Run: `uv sync --dev`

Expected: `pytest-cov` installed, no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py pyproject.toml uv.lock
git commit -m "test: add shared patch_llm fixture and pytest-cov dependency"
```

---

## Task 3: Migrate test_onboarding_agent.py

**Files:**
- Modify: `tests/integration/test_onboarding_agent.py`

**Background:** Three tests use a local `_make_fake_llm` helper that builds a `MagicMock`. Replace with `patch_llm` from `tests/conftest.py`. The existing `with patch("app.agents.onboarding.get_llm", return_value=fake_llm):` blocks become `with patch_llm("app.agents.onboarding", ["..."]):`.

- [ ] **Step 1: Run existing tests to confirm green baseline**

Run: `uv run pytest tests/integration/test_onboarding_agent.py -v`

Expected: 3 passed.

- [ ] **Step 2: Rewrite `tests/integration/test_onboarding_agent.py`**

```python
"""
Integration test for the onboarding LangGraph agent with a real PostgreSQL checkpointer.
"""

import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.onboarding import build_graph
from conftest import patch_llm


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
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/integration/test_onboarding_agent.py -v`

Expected: 3 passed. Fix any import errors before continuing.

- [ ] **Step 4: Confirm no MagicMock remains for LLM**

Run: `grep -n "MagicMock\|_make_fake_llm\|AsyncMock" tests/integration/test_onboarding_agent.py`

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_onboarding_agent.py
git commit -m "test: migrate onboarding_agent tests from MagicMock to patch_llm"
```

---

## Task 4: Migrate test_match_scoring.py

**Files:**
- Modify: `tests/integration/test_match_scoring.py`

**Background:** `_make_llm_mock` builds a `MagicMock` that manually sets `resp.tool_calls`. With the updated `ToolCapableFakeLLM` (Task 1), JSON response strings automatically produce `tool_calls`. Replace `_make_llm_mock` with `patch_llm`.

JSON response format for `record_score` tool: `{"score": <float>, "rationale": "...", "strengths": [...], "gaps": [...]}`.

- [ ] **Step 1: Run existing tests**

Run: `uv run pytest tests/integration/test_match_scoring.py -v`

Expected: 4 passed.

- [ ] **Step 2: Rewrite `tests/integration/test_match_scoring.py`**

```python
"""
Integration tests for match scoring pipeline — real Postgres, mocked LLM.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.match_service import list_applications, score_and_match
from conftest import patch_llm


async def _seed_profile(db_session) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"test-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer with 5 years experience.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _seed_job(
    db_session,
    title: str = "Software Engineer",
    salary: str | None = None,
    posted_at: datetime | None = None,
) -> Job:
    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title=title,
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="A great engineering role.",
        salary=salary,
        posted_at=posted_at,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_score_and_match_persists_all_scores(db_session):
    """All Application rows get their scores persisted regardless of threshold."""
    profile = await _seed_profile(db_session)
    jobs = [await _seed_job(db_session, title=f"Job {i}") for i in range(3)]

    # Scores: 0.9 (pass), 0.5 (fail), 0.7 (pass)
    responses = [
        '{"score": 0.9, "rationale": "Excellent match", "strengths": ["Python"], "gaps": []}',
        '{"score": 0.5, "rationale": "Weak match", "strengths": [], "gaps": ["Many missing"]}',
        '{"score": 0.7, "rationale": "Good match", "strengths": ["FastAPI"], "gaps": ["Go"]}',
    ]
    with patch_llm("app.agents.matching_agent", responses):
        scored = await score_and_match(profile, db_session, jobs=jobs)

    assert len(scored) == 2  # only 0.9 and 0.7 pass threshold

    result = await db_session.execute(
        select(Application).where(Application.profile_id == profile.id)
    )
    all_apps = result.scalars().all()
    assert len(all_apps) == 3
    assert all(a.match_score is not None for a in all_apps)

    statuses = {a.match_score: a.status for a in all_apps}
    assert statuses[0.5] == "auto_rejected"
    for a in all_apps:
        if a.match_score != 0.5:
            assert a.status == "pending_review"


@pytest.mark.asyncio
async def test_list_applications_excludes_auto_rejected(db_session):
    """list_applications(status='pending_review') never returns auto_rejected rows."""
    profile = await _seed_profile(db_session)
    job1 = await _seed_job(db_session, title="Good Match")
    job2 = await _seed_job(db_session, title="Poor Match")
    job3 = await _seed_job(db_session, title="Legacy Zombie")

    app1 = Application(
        job_id=job1.id, profile_id=profile.id, match_score=0.85, match_rationale="Great"
    )
    app2 = Application(
        job_id=job2.id,
        profile_id=profile.id,
        match_score=0.4,
        status="auto_rejected",
        match_rationale="Weak",
    )
    app3 = Application(job_id=job3.id, profile_id=profile.id)

    for a in [app1, app2, app3]:
        db_session.add(a)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session, status="pending_review")
    ids = [str(app.id) for app, _ in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids
    assert str(app3.id) not in ids


@pytest.mark.asyncio
async def test_list_applications_ordering(db_session):
    """Matches ordered: match_score DESC, salary non-null first, posted_at DESC."""
    profile = await _seed_profile(db_session)

    now = datetime.now(UTC)
    job_a = await _seed_job(db_session, "Job A", salary="$100k", posted_at=now - timedelta(days=1))
    job_b = await _seed_job(db_session, "Job B", salary=None, posted_at=now - timedelta(days=2))
    job_c = await _seed_job(db_session, "Job C", salary="$90k", posted_at=now)
    job_d = await _seed_job(db_session, "Job D", salary=None, posted_at=now - timedelta(days=3))

    score = 0.8
    for job in [job_a, job_b, job_c, job_d]:
        app = Application(
            job_id=job.id, profile_id=profile.id, match_score=score, match_rationale="Good"
        )
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    titles = [job.title for _, job in rows]

    null_salary_positions = [i for i, (_, j) in enumerate(rows) if j.salary is None]
    non_null_positions = [i for i, (_, j) in enumerate(rows) if j.salary is not None]
    assert max(non_null_positions) < min(null_salary_positions), (
        f"Non-null salary jobs should all precede null-salary jobs, got order: {titles}"
    )

    assert titles.index("Job C") < titles.index("Job A")
    assert titles.index("Job B") < titles.index("Job D")


@pytest.mark.asyncio
async def test_list_applications_returns_job_data(db_session):
    """list_applications returns (Application, Job) tuples — no N+1 queries needed."""
    profile = await _seed_profile(db_session)
    job = await _seed_job(db_session, title="Python Engineer", salary="$120k")

    app = Application(
        job_id=job.id, profile_id=profile.id, match_score=0.8, match_rationale="Great fit"
    )
    db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    assert len(rows) == 1

    returned_app, returned_job = rows[0]
    assert returned_app.id == app.id
    assert returned_job.id == job.id
    assert returned_job.title == "Python Engineer"
    assert returned_job.salary == "$120k"
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/integration/test_match_scoring.py -v`

Expected: 4 passed. If scores don't match, check that `ToolCapableFakeLLM._bound_tool_name` is being set (add a debug print in bind_tools if needed).

- [ ] **Step 4: Verify no MagicMock LLM usage remains**

Run: `grep -n "_make_llm_mock\|MagicMock\|mock_bound" tests/integration/test_match_scoring.py`

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_match_scoring.py
git commit -m "test: migrate match_scoring tests from MagicMock to patch_llm"
```

---

## Task 5: Migrate test_match_service.py

**Files:**
- Modify: `tests/unit/test_match_service.py`

**Background:** The unit test validates threshold logic and log output. The `_make_profile/job/application` factories return `MagicMock` — replace them with real `UserProfile`, `Job`, and `Application` SQLModel instances. This makes attribute access type-safe. Keep the `patch` decorators for `build_graph`, `profile_service`, and `get_or_create_application` (those are legitimate isolation patches).

- [ ] **Step 1: Run existing tests**

Run: `uv run pytest tests/unit/test_match_service.py -v`

Note the test names and count.

- [ ] **Step 2: Update the factory helpers in `tests/unit/test_match_service.py`**

Find and replace the three factory functions. The imports section at the top of the file needs these additions:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
```

Replace `_make_profile()`:

```python
def _make_profile() -> UserProfile:
    return UserProfile(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer.",
        target_roles=["Software Engineer"],
        seniority="senior",
        remote_ok=True,
    )
```

Replace `_make_job(job_id=None)`:

```python
def _make_job(job_id: uuid.UUID | None = None) -> Job:
    return Job(
        id=job_id or uuid.uuid4(),
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title="Software Engineer",
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="A great job.",
    )
```

Replace `_make_application(app_id=None, job_id=None)`:

```python
def _make_application(
    app_id: uuid.UUID | None = None, job_id: uuid.UUID | None = None
) -> Application:
    return Application(
        id=app_id or uuid.uuid4(),
        job_id=job_id or uuid.uuid4(),
        profile_id=uuid.uuid4(),
        status="pending_review",
        match_score=None,
        match_rationale=None,
        match_strengths=[],
        match_gaps=[],
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_match_service.py -v`

Expected: same number of tests pass as in Step 1. Fix any attribute errors (e.g., tests that access `.id` with `str()` calls — real objects have `.id` as `uuid.UUID`, not a string).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_match_service.py
git commit -m "test: replace MagicMock model factories with real SQLModel instances in test_match_service"
```

---

## Task 6: New test_generation_agent.py

**Files:**
- Create: `tests/unit/test_generation_agent.py`

**Background:** The generation agent has no tests. Tests call `build_graph()` with `MemorySaver` (no real DB needed). `save_documents_node` imports `get_session_factory` and `save_documents` at call time — patch both so the unit test doesn't touch a real DB. The graph uses `interrupt_before=["review"]`, so the first `ainvoke` pauses before `review_node`. A second call with `None` enters `review_node` which calls `interrupt({...})` (pauses again). A third call with `Command(resume=value)` completes the graph.

- [ ] **Step 1: Write failing test file**

```python
"""
Unit tests for the generation LangGraph agent.

Uses MemorySaver (in-memory checkpointer) and ToolCapableFakeLLM to avoid
real DB or LLM calls. `save_documents_node` is patched at the module boundary.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.generation_agent import build_graph
from conftest import patch_llm

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
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_generation_agent.py -v`

Expected: 4 passed. Common failure causes:
- `MemorySaver` import path changed: try `from langgraph.checkpoint.memory import MemorySaver`
- `Command` import: `from langgraph.types import Command`
- Patch targets wrong: verify `app.database.get_session_factory` is what `save_documents_node` imports

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_generation_agent.py
git commit -m "test: add generation_agent unit tests covering doc generation and interrupt/resume"
```

---

## Task 7: New test_rate_limit_service.py

**Files:**
- Create: `tests/integration/test_rate_limit_service.py`

**Background:** `rate_limit_service.py` has no dedicated tests. It uses real Postgres (`INSERT ... ON CONFLICT DO UPDATE`), so this is an integration test. The `_window_start()` helper is patched directly to test window-reset behaviour without real clock dependencies.

- [ ] **Step 1: Write the test file**

```python
"""
Integration tests for the rate limit and usage quota service.

Tests call service functions directly against the testcontainers Postgres DB.
The service commits inside each call, so each invocation is permanent within the test.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services.rate_limit_service import check_daily_quota, check_rate_limit


@pytest.mark.asyncio
async def test_check_rate_limit_passes_under_limit(db_session):
    key = f"rl-test-{uuid.uuid4()}"
    await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    # No exception raised = pass


@pytest.mark.asyncio
async def test_check_rate_limit_raises_at_limit(db_session):
    """After limit+1 calls, the (limit+1)th call raises HTTP 429."""
    key = f"rl-test-{uuid.uuid4()}"
    for _ in range(3):
        await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_check_rate_limit_different_keys_are_independent(db_session):
    """Two different keys do not share counters."""
    key_a = f"rl-a-{uuid.uuid4()}"
    key_b = f"rl-b-{uuid.uuid4()}"
    for _ in range(3):
        await check_rate_limit(key_a, limit=3, window_seconds=3600, session=db_session)
    # key_b should still be at 0 — no exception
    await check_rate_limit(key_b, limit=3, window_seconds=3600, session=db_session)


@pytest.mark.asyncio
async def test_sliding_window_resets_counter(db_session):
    """A new window start means a fresh counter — previous window's exhausted limit doesn't carry over."""
    key = f"rl-window-{uuid.uuid4()}"
    limit = 1
    window_seconds = 3600

    t1 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 1, 2, 0, 0, tzinfo=UTC)  # 2 hours later — new window

    with patch(
        "app.services.rate_limit_service._window_start",
        return_value=t1,
    ):
        # Exhaust limit in window 1
        await check_rate_limit(key, limit=limit, window_seconds=window_seconds, session=db_session)
        with pytest.raises(HTTPException):
            await check_rate_limit(
                key, limit=limit, window_seconds=window_seconds, session=db_session
            )

    with patch(
        "app.services.rate_limit_service._window_start",
        return_value=t2,
    ):
        # Window 2 is fresh — should not raise
        await check_rate_limit(key, limit=limit, window_seconds=window_seconds, session=db_session)


@pytest.mark.asyncio
async def test_check_daily_quota_passes_under_limit(db_session):
    user_id = uuid.uuid4()
    await check_daily_quota(user_id, action="resume_upload", limit=3, session=db_session)
    # No exception = pass


@pytest.mark.asyncio
async def test_check_daily_quota_raises_at_limit(db_session):
    """After limit+1 calls with the same user+action+day, raises HTTP 429."""
    user_id = uuid.uuid4()
    for _ in range(3):
        await check_daily_quota(
            user_id, action="resume_upload", limit=3, session=db_session
        )
    with pytest.raises(HTTPException) as exc_info:
        await check_daily_quota(
            user_id, action="resume_upload", limit=3, session=db_session
        )
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_check_daily_quota_different_users_are_independent(db_session):
    """Two different users do not share daily quota counters."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    for _ in range(3):
        await check_daily_quota(
            user_a, action="resume_upload", limit=3, session=db_session
        )
    # user_b should still be at 0
    await check_daily_quota(user_b, action="resume_upload", limit=3, session=db_session)
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/integration/test_rate_limit_service.py -v`

Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rate_limit_service.py
git commit -m "test: add rate_limit_service integration tests for sliding window and daily quota"
```

---

## Task 8: New test_auth_oauth.py

**Files:**
- Create: `tests/integration/test_auth_oauth.py`

**Background:** Tests the JWT auth path in `app/api/deps.py`. When `AUTH_ENABLED=true`, requests without a valid Bearer token return 401. The fixture mints JWTs using `PyJWT` matching the exact decode call in `deps.py`: `jwt.decode(token, secret, algorithms=["HS256"], audience=["fastapi-users:auth"])`.

The `app` object is module-level in `app.main`. The `patch_settings` autouse fixture in integration conftest already resets `_settings` — the auth test adds `AUTH_ENABLED=true` and `JWT_SECRET` on top.

- [ ] **Step 1: Write the test file**

```python
"""
Integration tests for JWT authentication path.

AUTH_ENABLED=true is set per fixture. A User row is seeded in the test DB.
JWTs are minted with PyJWT using the test secret and the same payload format
that app/api/deps.py decodes.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.main import app
from app.models.user import User

_TEST_JWT_SECRET = "test-jwt-secret-is-exactly-32by!"


def _mint_jwt(user_id: uuid.UUID, expired: bool = False) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=(-1 if expired else 86400))
    payload = {
        "sub": str(user_id),
        "aud": ["fastapi-users:auth"],
        "exp": exp,
        "iat": now,
    }
    return pyjwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
async def auth_client(db_session, monkeypatch):
    """
    HTTP client with AUTH_ENABLED=true against the real testcontainers DB.
    Returns (client, seeded_user_id).
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("CRON_SHARED_SECRET", "real-cron-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "fake-client-secret")

    import app.config as cfg
    monkeypatch.setattr(cfg, "_settings", None)

    # Seed a user that JWTs will reference
    user = User(
        id=uuid.uuid4(),
        email="auth-test@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user.id


@pytest.mark.asyncio
async def test_protected_route_without_token_returns_401(auth_client):
    client, _ = auth_client
    response = await client.get("/api/applications")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_valid_jwt_returns_200(auth_client):
    client, user_id = auth_client
    token = _mint_jwt(user_id)
    response = await client.get(
        "/api/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_with_expired_jwt_returns_401(auth_client):
    client, user_id = auth_client
    token = _mint_jwt(user_id, expired=True)
    response = await client.get(
        "/api/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_invalid_jwt_returns_401(auth_client):
    client, _ = auth_client
    response = await client.get(
        "/api/applications", headers={"Authorization": "Bearer not-a-valid-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_with_unknown_user_id_returns_401(auth_client):
    """A valid JWT whose sub does not exist in the DB returns 401."""
    client, _ = auth_client
    token = _mint_jwt(uuid.uuid4())  # random user_id not in DB
    response = await client.get(
        "/api/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/integration/test_auth_oauth.py -v`

Expected: 5 passed. Common failures:
- The `app` lifespan tries to set up LangGraph checkpointer. If it crashes, add a try/except around the checkpointer setup (it may already handle `DuplicateTable` — check `app/main.py` lifespan). If checkpointer setup fails with a different error, you may need to mock it: `patch("app.main.AsyncPostgresSaver")`.
- `User` model import path: `from app.models.user import User`

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_auth_oauth.py
git commit -m "test: add JWT auth integration tests for AUTH_ENABLED=true path"
```

---

## Task 9: New test_application_service_lifecycle.py

**Files:**
- Create: `tests/integration/test_application_service_lifecycle.py`

**Background:** `generate_materials()` with `checkpointer=None` uses the `_generate_direct` fallback path. Since `ENVIRONMENT=test`, `_generate_direct` calls `get_fake_llm("generation")` — no real LLM needed. All DB operations use the testcontainers `db_session`.

The seeding order matters: `User → UserProfile → Job → Application`.

- [ ] **Step 1: Write the test file**

```python
"""
Integration tests for application_service state transitions and document persistence.
"""

import uuid

import pytest
from sqlmodel import select

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.application_service import generate_materials, save_documents


async def _seed_application(db_session) -> tuple[Application, Job, UserProfile]:
    """Create User → UserProfile → Job → Application and return all three."""
    user = User(id=uuid.uuid4(), email=f"lifecycle-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title="Python Engineer",
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="Python backend role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    return app_row, job, profile


@pytest.mark.asyncio
async def test_save_documents_creates_row(db_session):
    """save_documents() inserts a GeneratedDocument row."""
    app_row, _, _ = await _seed_application(db_session)
    docs = [{"doc_type": "tailored_resume", "content_md": "# Resume", "generation_model": "test"}]

    await save_documents(str(app_row.id), docs, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].doc_type == "tailored_resume"
    assert rows[0].content_md == "# Resume"


@pytest.mark.asyncio
async def test_save_documents_upserts_on_retry(db_session):
    """Calling save_documents() twice with the same doc_type updates, not duplicates."""
    app_row, _, _ = await _seed_application(db_session)
    docs = [{"doc_type": "tailored_resume", "content_md": "# Draft 1", "generation_model": "test"}]
    await save_documents(str(app_row.id), docs, db_session)

    docs2 = [
        {"doc_type": "tailored_resume", "content_md": "# Final Resume", "generation_model": "test"}
    ]
    await save_documents(str(app_row.id), docs2, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1, "Upsert should produce exactly one row"
    assert rows[0].content_md == "# Final Resume"


@pytest.mark.asyncio
async def test_generate_materials_not_found_no_error(db_session):
    """generate_materials() with a nonexistent UUID returns silently."""
    await generate_materials(uuid.uuid4(), db_session, checkpointer=None)
    # No exception = pass


@pytest.mark.asyncio
async def test_generate_materials_max_attempts_skipped(db_session):
    """Application with generation_attempts=3 is skipped without changing status."""
    app_row, _, _ = await _seed_application(db_session)
    app_row.generation_attempts = 3
    db_session.add(app_row)
    await db_session.commit()

    await generate_materials(app_row.id, db_session, checkpointer=None)

    await db_session.refresh(app_row)
    # status should still be "pending_review" (not "generating" or "ready")
    assert app_row.generation_status == "none"
    assert app_row.generation_attempts == 3


@pytest.mark.asyncio
async def test_generate_materials_sets_status_to_ready(db_session):
    """
    generate_materials() with checkpointer=None uses _generate_direct.
    Since ENVIRONMENT=test, get_fake_llm("generation") is used — no real LLM call.
    After completion: generation_status="ready" and GeneratedDocument rows exist.
    """
    app_row, _, _ = await _seed_application(db_session)

    await generate_materials(app_row.id, db_session, checkpointer=None)

    await db_session.refresh(app_row)
    assert app_row.generation_status == "ready"

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    docs = result.scalars().all()
    assert len(docs) >= 2  # at minimum: tailored_resume + cover_letter
    doc_types = {d.doc_type for d in docs}
    assert "tailored_resume" in doc_types
    assert "cover_letter" in doc_types
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/integration/test_application_service_lifecycle.py -v`

Expected: 5 passed. If `test_generate_materials_sets_status_to_ready` fails, check that `_generate_direct` uses `get_fake_llm("generation")` when `ENVIRONMENT=test` — confirm `patch_settings` autouse fixture is setting `ENVIRONMENT=test`.

- [ ] **Step 3: Run the full test suite to check for regressions**

Run: `uv run pytest tests/unit/ tests/integration/ -q`

Expected: all pass (113+ tests).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_application_service_lifecycle.py
git commit -m "test: add application_service lifecycle tests for save_documents and generate_materials"
```

---

## Task 10: MSW setup + frontend tooling

**Files:**
- Modify: `frontend/package.json` (add msw, @vitest/coverage-v8)
- Create: `frontend/src/test/handlers.ts`
- Create: `frontend/src/test/server.ts`
- Modify: `frontend/src/test/setup.ts`
- Modify: `frontend/vite.config.ts` (add coverage config)

- [ ] **Step 1: Install MSW and coverage packages**

Run: `cd frontend && npm install --save-dev msw@^2 @vitest/coverage-v8`

Expected: packages added to `package.json` devDependencies, no errors.

- [ ] **Step 2: Initialize MSW service worker file**

Run: `cd frontend && npx msw init public/ --save`

Expected: creates `public/mockServiceWorker.js`. Adds `"msw": {"workerDirectory": "public"}` to `package.json`.

- [ ] **Step 3: Create `frontend/src/test/handlers.ts`**

```typescript
import { http, HttpResponse } from 'msw'

export const handlers = [
  http.get('/api/me', () =>
    HttpResponse.json({ id: '00000000-0000-0000-0000-000000000001', email: 'test@test.com' })
  ),
  http.get('/api/status', () =>
    HttpResponse.json({ budget_exhausted: false, resumes_at: null })
  ),
  http.get('/api/applications', () => HttpResponse.json([])),
  http.get('/api/profile', () => HttpResponse.json(null)),
]
```

- [ ] **Step 4: Create `frontend/src/test/server.ts`**

```typescript
import { setupServer } from 'msw/node'
import { handlers } from './handlers'

export const server = setupServer(...handlers)
```

- [ ] **Step 5: Update `frontend/src/test/setup.ts`**

Replace the file contents entirely:

```typescript
import '@testing-library/jest-dom'
import { server } from './server'

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

- [ ] **Step 6: Update `frontend/vite.config.ts` test block**

Add `coverage` to the existing `test` block. The full updated `test` block:

```typescript
test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/**/*.test.*', 'src/main.tsx'],
    },
  },
```

(No threshold yet — that's set in Task 14 after measuring baseline.)

- [ ] **Step 7: Run existing frontend tests to verify setup doesn't break them**

Run: `cd frontend && npm test`

Expected: 6 passed (RequireAuth + MatchCard/Matches existing tests). If MSW warns about unhandled requests, add the missing handler to `handlers.ts`.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json \
        frontend/public/mockServiceWorker.js \
        frontend/src/test/handlers.ts frontend/src/test/server.ts \
        frontend/src/test/setup.ts frontend/vite.config.ts
git commit -m "test: add MSW v2 setup and @vitest/coverage-v8 for frontend tests"
```

---

## Task 11: BudgetBanner.test.tsx

**Files:**
- Create: `frontend/src/components/BudgetBanner.test.tsx`

**Background:** `BudgetBanner` calls `api.getStatus()` on mount and every 60s. It renders nothing when `budget_exhausted=false`, renders a banner message when `budget_exhausted=true`. Uses MSW to control `/api/status` responses per test.

- [ ] **Step 1: Write the test**

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import BudgetBanner from './BudgetBanner'

describe('BudgetBanner', () => {
  it('renders nothing when budget is not exhausted', async () => {
    // default handler: budget_exhausted=false
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.queryByText(/AI features paused/)).not.toBeInTheDocument()
    })
  })

  it('renders banner when budget_exhausted is true', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({ budget_exhausted: true, resumes_at: null })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.getByText(/AI features paused until next month/)).toBeInTheDocument()
    })
  })

  it('renders the formatted resumes_at date when provided', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({
          budget_exhausted: true,
          resumes_at: '2025-05-01T00:00:00Z',
        })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      // Date is formatted as "Month Day" (e.g. "May 1")
      expect(screen.getByText(/AI features paused until May 1/)).toBeInTheDocument()
    })
  })

  it('renders "next month" when resumes_at is null', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({ budget_exhausted: true, resumes_at: null })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.getByText(/next month/)).toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run the test**

Run: `cd frontend && npm test -- BudgetBanner`

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BudgetBanner.test.tsx
git commit -m "test: add BudgetBanner component tests"
```

---

## Task 12: MatchCard.test.tsx (delete old, create new)

**Files:**
- Delete: `frontend/src/test/MatchCard.test.tsx`
- Create: `frontend/src/components/MatchCard.test.tsx`

**Background:** `frontend/src/test/MatchCard.test.tsx` tests the `Matches` page, not the `MatchCard` component — misleading. Delete it and create a proper component test. `MatchCard` uses `useMutation` from `@tanstack/react-query` so the render must be wrapped in `QueryClientProvider`.

- [ ] **Step 1: Delete the mislabeled test**

Run: `rm frontend/src/test/MatchCard.test.tsx`

- [ ] **Step 2: Write `frontend/src/components/MatchCard.test.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { MatchCard } from './MatchCard'
import type { Application } from '../api/client'

function makeApp(overrides: Partial<Application> = {}): Application {
  return {
    id: 'app-1',
    status: 'pending_review',
    generation_status: 'none',
    match_score: 0.85,
    match_rationale: 'Strong Python background matches the role requirements.',
    match_strengths: ['Python', 'FastAPI'],
    match_gaps: ['Go experience'],
    user_interest: null,
    created_at: new Date().toISOString(),
    job: {
      id: 'job-1',
      title: 'Python Engineer',
      company_name: 'Acme Corp',
      location: 'Remote',
      workplace_type: 'remote',
      salary: '$120k',
      contract_type: 'full-time',
      description_md: 'A great role.',
      apply_url: 'https://example.com/apply',
      ats_type: null,
      supports_api_apply: false,
      posted_at: null,
    },
    ...overrides,
  }
}

function renderCard(app: Application) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MatchCard app={app} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('MatchCard', () => {
  it('renders job title, company, and match score badge', () => {
    renderCard(makeApp())
    expect(screen.getByText('Python Engineer')).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('85% match')).toBeInTheDocument()
  })

  it('renders strengths and gaps', () => {
    renderCard(makeApp())
    expect(screen.getByText(/Python/)).toBeInTheDocument()
    expect(screen.getByText(/Go experience/)).toBeInTheDocument()
  })

  it('thumbs-up button sends PATCH with interested', async () => {
    const user = userEvent.setup()
    let patchedBody: unknown
    server.use(
      http.patch('/api/applications/:id/interest', async ({ request }) => {
        patchedBody = await request.json()
        return HttpResponse.json(null)
      })
    )
    renderCard(makeApp())
    await user.click(screen.getByLabelText('Mark as interested'))
    await waitFor(() => {
      expect(patchedBody).toEqual({ interest: 'interested' })
    })
  })

  it('thumbs-up again toggles interest back to null', async () => {
    const user = userEvent.setup()
    const bodies: unknown[] = []
    server.use(
      http.patch('/api/applications/:id/interest', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(null)
      })
    )
    renderCard(makeApp({ user_interest: 'interested' }))
    await user.click(screen.getByLabelText('Mark as interested'))
    await waitFor(() => {
      expect(bodies[bodies.length - 1]).toEqual({ interest: null })
    })
  })

  it('review link points to /matches/app-1', () => {
    renderCard(makeApp())
    const link = screen.getByText('Review →')
    expect(link.closest('a')).toHaveAttribute('href', '/matches/app-1')
  })

  it('dismiss button sends PATCH with dismissed status', async () => {
    const user = userEvent.setup()
    let patchedBody: unknown
    server.use(
      http.patch('/api/applications/app-1', async ({ request }) => {
        patchedBody = await request.json()
        return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
      })
    )
    renderCard(makeApp())
    await user.click(screen.getByText('Dismiss'))
    await waitFor(() => {
      expect(patchedBody).toEqual({ status: 'dismissed' })
    })
  })
})
```

- [ ] **Step 3: Install `@testing-library/user-event` if not already present**

Run: `cd frontend && npm ls @testing-library/user-event`

If not listed, run: `cd frontend && npm install --save-dev @testing-library/user-event`

- [ ] **Step 4: Run the test**

Run: `cd frontend && npm test -- MatchCard`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MatchCard.test.tsx
git commit -m "test: add MatchCard component tests, delete mislabeled Matches page test"
```

---

## Task 13: AuthContext.test.tsx

**Files:**
- Create: `frontend/src/context/AuthContext.test.tsx`

**Background:** `AuthContext` reads `sessionStorage` for a token, calls `api.getMe()`, sets `user`. Tests control `/api/me` via MSW. `signOut` calls `window.location.href = '/'` — mock this.

- [ ] **Step 1: Write the test**

```tsx
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { AuthProvider, useAuth } from './AuthContext'
import { MemoryRouter } from 'react-router-dom'

function AuthStatus() {
  const { user, loading, token } = useAuth()
  if (loading) return <div>loading</div>
  if (!user) return <div>no user</div>
  return <div>user:{user.email}</div>
}

function renderAuth() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AuthStatus />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('starts in loading state then resolves', async () => {
    renderAuth()
    expect(screen.getByText('loading')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('loading')).not.toBeInTheDocument()
    })
  })

  it('sets user when getMe succeeds (no token)', async () => {
    // Default handler returns a user
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('user:test@test.com')).toBeInTheDocument()
    })
  })

  it('keeps user=null when getMe fails and no token in sessionStorage', async () => {
    server.use(http.get('/api/me', () => HttpResponse.error()))
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('no user')).toBeInTheDocument()
    })
  })

  it('calls getMe when token is present in sessionStorage', async () => {
    sessionStorage.setItem('access_token', 'test-token')
    let authHeader: string | null = null
    server.use(
      http.get('/api/me', ({ request }) => {
        authHeader = request.headers.get('Authorization')
        return HttpResponse.json({ id: '1', email: 'token-user@test.com' })
      })
    )
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('user:token-user@test.com')).toBeInTheDocument()
    })
    expect(authHeader).toBe('Bearer test-token')
  })

  it('clears token from sessionStorage when getMe fails with stored token', async () => {
    sessionStorage.setItem('access_token', 'bad-token')
    server.use(http.get('/api/me', () => new HttpResponse(null, { status: 401 })))
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('no user')).toBeInTheDocument()
    })
    expect(sessionStorage.getItem('access_token')).toBeNull()
  })

  it('signOut clears user and token', async () => {
    // Mock window.location.href setter
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '' },
    })

    function SignOutButton() {
      const { signOut, user } = useAuth()
      return (
        <div>
          {user ? <span>logged-in</span> : <span>logged-out</span>}
          <button onClick={signOut}>sign out</button>
        </div>
      )
    }

    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AuthProvider>
          <SignOutButton />
        </AuthProvider>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('logged-in')).toBeInTheDocument()
    })

    await user.click(screen.getByText('sign out'))
    expect(screen.getByText('logged-out')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test**

Run: `cd frontend && npm test -- AuthContext`

Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/context/AuthContext.test.tsx
git commit -m "test: add AuthContext tests for token hydration, getMe, and signOut"
```

---

## Task 14: client.test.ts

**Files:**
- Create: `frontend/src/api/client.test.ts`

**Background:** Tests `apiFetch` error handling and the `sendMessage` SSE streaming parser. These tests mock `fetch` via `vi.spyOn` to control exact responses — this tests the client's internal logic rather than HTTP contract (which MSW covers for component tests).

- [ ] **Step 1: Write the test**

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from './client'

function mockFetch(status: number, body: unknown, headers?: Record<string, string>) {
  return vi.spyOn(global, 'fetch').mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    })
  )
}

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  describe('apiFetch error handling', () => {
    it('returns parsed JSON on 200', async () => {
      mockFetch(200, { id: '1', email: 'test@test.com' })
      const result = await api.getMe()
      expect(result).toEqual({ id: '1', email: 'test@test.com' })
    })

    it('throws on 401', async () => {
      mockFetch(401, { detail: 'Not authenticated' })
      await expect(api.getMe()).rejects.toThrow('401')
    })

    it('throws on 500', async () => {
      mockFetch(500, { detail: 'Internal server error' })
      await expect(api.getApplications()).rejects.toThrow('500')
    })

    it('includes Authorization header when token is in sessionStorage', async () => {
      sessionStorage.setItem('access_token', 'my-token')
      const spy = mockFetch(200, { id: '1', email: 'x@test.com' })
      await api.getMe()
      const calledHeaders = (spy.mock.calls[0][1] as RequestInit)?.headers as Record<string, string>
      expect(calledHeaders['Authorization']).toBe('Bearer my-token')
    })

    it('setInterest sends PATCH with correct body', async () => {
      const spy = mockFetch(200, null)
      await api.setInterest('app-123', 'interested')
      expect(spy.mock.calls[0][0]).toBe('/api/applications/app-123/interest')
      const init = spy.mock.calls[0][1] as RequestInit
      expect(init.method).toBe('PATCH')
      expect(JSON.parse(init.body as string)).toEqual({ interest: 'interested' })
    })
  })

  describe('sendMessage SSE streaming', () => {
    it('calls onChunk for each data line with content', async () => {
      const encoder = new TextEncoder()
      const chunks = [
        'data: {"content": "Hello"}\n\n',
        'data: {"content": " world"}\n\n',
        'data: [DONE]\n\n',
      ]
      let i = 0
      const stream = new ReadableStream({
        pull(controller) {
          if (i < chunks.length) {
            controller.enqueue(encoder.encode(chunks[i++]))
          } else {
            controller.close()
          }
        },
      })

      vi.spyOn(global, 'fetch').mockResolvedValueOnce(
        new Response(stream, { status: 200 })
      )

      const received: string[] = []
      await api.sendMessage('hello', (chunk) => received.push(chunk))
      expect(received).toEqual(['Hello', ' world'])
    })

    it('ignores non-data SSE lines', async () => {
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            encoder.encode('event: message\ndata: {"content": "Hi"}\n\ndata: [DONE]\n\n')
          )
          controller.close()
        },
      })

      vi.spyOn(global, 'fetch').mockResolvedValueOnce(
        new Response(stream, { status: 200 })
      )

      const received: string[] = []
      await api.sendMessage('hi', (chunk) => received.push(chunk))
      expect(received).toEqual(['Hi'])
    })
  })
})
```

- [ ] **Step 2: Add helper type for `getApplications`**

The test calls `api.getApplications()`. Check `frontend/src/api/client.ts` — it's `api.listApplications()`. Update the test:

```typescript
await expect(api.listApplications()).rejects.toThrow('500')
```

- [ ] **Step 3: Run the test**

Run: `cd frontend && npm test -- client.test`

Expected: 7 passed.

- [ ] **Step 4: Run all frontend tests**

Run: `cd frontend && npm test`

Expected: all tests pass (including RequireAuth, BudgetBanner, MatchCard, AuthContext).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.test.ts
git commit -m "test: add api client unit tests for error handling and SSE streaming"
```

---

## Task 15: Coverage gates + pre-commit hook

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Measure backend coverage baseline**

Run: `uv run pytest tests/unit/ tests/integration/ --cov=app --cov-report=term-missing 2>&1 | tail -5`

Note the `TOTAL` line percentage (e.g. `TOTAL ... 62%`). The threshold will be `(this number) - 2`.

- [ ] **Step 2: Measure frontend coverage baseline**

Run: `cd frontend && npm run test -- --coverage 2>&1 | tail -10`

Note the `Lines` percentage. The threshold will be `(this number) - 2`.

- [ ] **Step 3: Update backend CI step in `.github/workflows/ci.yml`**

Find the `Unit tests` step and update the run command (replace `<N>` with the measured percentage minus 2):

```yaml
- name: Unit tests
  run: uv run pytest tests/unit/ tests/integration/ -v --cov=app --cov-fail-under=<N>
  env:
    DATABASE_URL: postgresql+asyncpg://test:test@localhost/test
    GOOGLE_API_KEY: fake-test-key
    ENVIRONMENT: test
```

Note: unit tests in CI do NOT have a real Postgres service — integration tests that need one use testcontainers. Adjust if needed: the integration test step may need the `--cov` flag added there instead.

Actually, looking at the CI file: there's one combined step "Integration + E2E tests" without a Postgres service. The testcontainers approach spins up its own container. Add `--cov` to both steps:

Unit tests step (no change to run line except adding --cov flags):
```yaml
- name: Unit tests
  run: uv run pytest tests/unit/ --cov=app --cov-report=term --cov-fail-under=<N> -v
```

Integration + E2E step:
```yaml
- name: Integration + E2E tests
  run: uv run pytest tests/integration/ tests/e2e/ --cov=app --cov-append --cov-report=term -v
```

Remove `--cov-fail-under` from the integration step (enforce only on combined run, or keep it in unit only).

- [ ] **Step 4: Update frontend CI step in `.github/workflows/ci.yml`**

Find the `Test` step in the `frontend` job:

```yaml
- name: Test
  run: cd frontend && npm run test -- --coverage --coverage.thresholds.lines=<M>
```

Where `<M>` is frontend baseline minus 2.

Also update `frontend/vite.config.ts` to add the threshold permanently:

```typescript
coverage: {
  provider: 'v8',
  reporter: ['text', 'lcov'],
  include: ['src/**/*.{ts,tsx}'],
  exclude: ['src/test/**', 'src/**/*.test.*', 'src/main.tsx'],
  thresholds: {
    lines: <M>,
  },
},
```

- [ ] **Step 5: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]
        pass_filenames: true
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]
        pass_filenames: true
      - id: unit-tests
        name: unit tests (pre-push)
        entry: uv run pytest tests/unit -x -q
        language: system
        pass_filenames: false
        stages: [pre-push]
```

- [ ] **Step 6: Install pre-commit**

Run: `uv add --dev pre-commit`

Run: `uv run pre-commit install && uv run pre-commit install --hook-type pre-push`

- [ ] **Step 7: Update CLAUDE.md to document pre-commit install**

In `CLAUDE.md` under the "Commands" section, add:

```bash
# Pre-commit hooks (run automatically on commit/push after install)
uv run pre-commit install                    # lint on commit
uv run pre-commit install --hook-type pre-push  # unit tests on push
uv run pre-commit run --all-files            # run manually on all files
```

- [ ] **Step 8: Run full test suite one final time**

Run: `uv run pytest tests/unit/ tests/integration/ -q`

Expected: all pass.

Run: `cd frontend && npm test`

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/ci.yml .pre-commit-config.yaml CLAUDE.md frontend/vite.config.ts uv.lock pyproject.toml
git commit -m "chore: add coverage gates in CI and pre-commit hook for lint + unit tests"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| Shared scripted-LLM fixture in `tests/conftest.py` | Task 2 |
| ToolCapableFakeLLM produces tool_calls from JSON | Task 1 |
| Migrate `test_onboarding_agent.py` | Task 3 |
| Migrate `test_match_scoring.py` | Task 4 |
| Migrate `test_match_service.py` | Task 5 |
| `tests/unit/test_generation_agent.py` | Task 6 |
| `tests/integration/test_rate_limit_service.py` | Task 7 |
| `tests/integration/test_auth_oauth.py` | Task 8 |
| `tests/integration/test_application_service_lifecycle.py` | Task 9 |
| MSW setup + frontend tooling | Task 10 |
| `BudgetBanner.test.tsx` | Task 11 |
| `MatchCard.test.tsx` (delete old, create new) | Task 12 |
| `AuthContext.test.tsx` | Task 13 |
| `client.test.ts` | Task 14 |
| Coverage gates in CI | Task 15 |
| Pre-commit hook | Task 15 |

All spec requirements are covered.

**Type consistency check:** `patch_llm` defined in Task 2 used in Tasks 3, 4, 6 — same signature `(module_path: str, responses: list[str])`. ✓

**Placeholder check:** No TBD. Task 15 Step 3-4 has `<N>` and `<M>` placeholders that the implementer fills in after measuring the real baseline. This is intentional — the baseline doesn't exist until the tests are written. ✓
