"""
Integration tests for the LangGraph interrupt/resume contract in
app.agents.generation_agent.build_graph.

These tests lock in the lifecycle:
  pending -> generating -> awaiting_review (interrupt) -> ready (after resume)

- Tests 1-3 (flipped green in PR 9b) cover the interrupt pause, the approval
  resume, and the regenerate-loop resume.
- Test 4 asserts that generate_materials() raises RuntimeError when called
  without a checkpointer (the _generate_direct fallback was removed in PR 9a
  of the stabilization plan, so the LangGraph path is mandatory).
"""

import uuid
from unittest import mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlmodel import select

import app.agents.generation_agent as gen_agent_mod
from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.application_service import generate_materials

# ---------------------------------------------------------------------------
# Shared seed helper
# ---------------------------------------------------------------------------


async def _seed_application(db_session) -> tuple[Application, Job, UserProfile]:
    """Create User -> UserProfile -> Job -> Application and return all three."""
    user = User(id=uuid.uuid4(), email=f"interrupt-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Interrupt Test User",
        email="interrupt@test.com",
        base_resume_md="# Interrupt Test User\n\nSoftware engineer with Python experience.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title="Senior Python Engineer",
        company_name="Interrupt Corp",
        apply_url="https://example.com/apply",
        description_md="Build distributed systems with Python and PostgreSQL.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    return app_row, job, profile


def _memory_checkpointer() -> MemorySaver:
    """
    Return a MemorySaver; the fake LLM is injected by the ENVIRONMENT=test
    guard in generation_agent.get_llm() -- two responses suffice for
    generate_resume + generate_cover_letter.
    """
    return MemorySaver()


# ---------------------------------------------------------------------------
# Test 1 -- interrupt should leave status as "awaiting_review"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_interrupt_pauses_at_review(db_session):
    """
    After the first graph.ainvoke() call the graph should be paused at the
    'review' node (interrupt_before=["review"]).  The Application row should
    reflect generation_status='awaiting_review', and at least two
    GeneratedDocument rows (resume + cover letter) should already exist in the
    DB (written by save_documents_node before the interrupt).

    CURRENTLY FAILS because generate_materials() calls
      app.generation_status = "ready"
    after ainvoke() returns regardless of the interrupt.
    """
    from app.agents.generation_agent import build_graph

    app_row, _, _ = await _seed_application(db_session)
    checkpointer = _memory_checkpointer()

    await generate_materials(app_row.id, db_session, checkpointer=checkpointer)

    # Reload from DB -- generate_materials committed after ainvoke returned.
    await db_session.refresh(app_row)

    # BUG: currently "ready", should be "awaiting_review"
    assert app_row.generation_status == "awaiting_review", (
        f"Expected 'awaiting_review' while graph is paused at interrupt, "
        f"got '{app_row.generation_status}'"
    )

    # Documents should already be persisted by save_documents_node
    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    docs = result.scalars().all()
    assert len(docs) >= 2, f"Expected at least 2 docs pre-interrupt, got {len(docs)}"
    doc_types = {d.doc_type for d in docs}
    assert "tailored_resume" in doc_types
    assert "cover_letter" in doc_types

    # Verify the graph checkpoint shows a pending interrupt at 'review'
    graph = build_graph(checkpointer)
    thread_id = f"gen-{app_row.id}"
    config = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)

    assert "review" in state.next, (
        f"Expected graph to be paused before 'review', got next={state.next}"
    )
    # interrupt_before means the graph stops BEFORE executing review_node,
    # so interrupts list is empty but next=('review',) signals the pause.
    assert len(state.interrupts) == 0, (
        "interrupt_before pause should not yet have an Interrupt object in state.interrupts"
    )


# ---------------------------------------------------------------------------
# Test 2 -- resume with approval should transition to "ready"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_resume_after_approval(db_session):
    """
    The correct two-step flow:
    1. generate_materials() pauses at the interrupt -> status becomes "awaiting_review"
    2. Resume with Command(resume={"approved": True}) -> graph reaches END,
       status becomes "ready"

    CURRENTLY FAILS at step 1: generate_materials() jumps straight to "ready",
    so the pre-condition assertion below triggers before the resume step is reached.
    """
    from app.agents.generation_agent import build_graph

    app_row, _, _ = await _seed_application(db_session)
    checkpointer = _memory_checkpointer()
    thread_id = f"gen-{app_row.id}"
    config = {"configurable": {"thread_id": thread_id}}

    # Step 1: run until interrupt -- status should be "awaiting_review" here.
    await generate_materials(app_row.id, db_session, checkpointer=checkpointer)
    await db_session.refresh(app_row)

    # BUG: currently "ready"; this assertion is what makes the test xfail today.
    assert app_row.generation_status == "awaiting_review", (
        f"Pre-condition: expected 'awaiting_review' after first ainvoke, "
        f"got '{app_row.generation_status}'"
    )

    # Step 2: resume with approval -- only reached once step 1 is fixed.
    graph = build_graph(checkpointer)
    await graph.ainvoke(Command(resume={"regenerate": False, "approved": True}), config)

    state = await graph.aget_state(config)
    assert state.next == (), f"Expected graph at END after resume, got next={state.next}"

    await db_session.refresh(app_row)
    assert app_row.generation_status == "ready", (
        f"Expected 'ready' after resume+approval, got '{app_row.generation_status}'"
    )


# ---------------------------------------------------------------------------
# Test 3 -- regenerate decision loops back through load_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_regenerate_loops_back_to_load_context(db_session):
    """
    The correct regenerate flow:
    1. generate_materials() pauses -> status "awaiting_review"
    2. Resume with Command(resume={"regenerate": True}) -> graph re-enters
       load_context, re-generates docs, pauses at review again
    3. New docs should differ from the first-pass docs (fake LLM cycles responses)

    CURRENTLY FAILS at step 1: generate_materials() jumps to "ready" immediately,
    so the pre-condition assertion below triggers.
    """
    from app.agents.generation_agent import build_graph

    app_row, _, _ = await _seed_application(db_session)

    # Provide enough fake responses for two full generation passes (resume + regen)
    extra_llm = FakeListChatModel(
        responses=[
            "Tailored resume content here.",
            "Tailored cover letter content here.",
            "Regenerated resume content second pass.",
            "Regenerated cover letter content second pass.",
        ]
    )
    checkpointer = _memory_checkpointer()
    thread_id = f"gen-{app_row.id}"
    config = {"configurable": {"thread_id": thread_id}}

    with mock.patch.object(gen_agent_mod, "get_llm", return_value=extra_llm):
        graph = build_graph(checkpointer)

        # Step 1: run to first interrupt -- status should be "awaiting_review".
        await generate_materials(app_row.id, db_session, checkpointer=checkpointer)
        await db_session.refresh(app_row)

        # BUG: currently "ready"; this assertion is what makes the test xfail today.
        assert app_row.generation_status == "awaiting_review", (
            f"Pre-condition: expected 'awaiting_review' after first ainvoke, "
            f"got '{app_row.generation_status}'"
        )

        # Capture first-pass doc content -- only reached once step 1 is fixed.
        result = await db_session.execute(
            select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
        )
        first_docs = {d.doc_type: d.content_md for d in result.scalars().all()}

        # Step 2: resume with regenerate=True -- graph should loop back.
        await graph.ainvoke(Command(resume={"regenerate": True}), config)

        state = await graph.aget_state(config)
        assert "review" in state.next, (
            f"Expected graph paused at review again after regenerate, got next={state.next}"
        )

        result2 = await db_session.execute(
            select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
        )
        second_docs = {d.doc_type: d.content_md for d in result2.scalars().all()}
        assert second_docs.get("tailored_resume") != first_docs.get("tailored_resume"), (
            "Expected tailored_resume content to change after regenerate loop"
        )


# ---------------------------------------------------------------------------
# Test 4 -- checkpointer=None raises RuntimeError (no xfail; unchanged in PR 9b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_without_checkpointer_raises(db_session):
    """
    generate_materials() requires a LangGraph checkpointer. The silent
    _generate_direct fallback was removed in PR 9a of the stabilization
    plan, so calling with checkpointer=None now raises RuntimeError
    before any status mutation happens.

    Expected outcome:
    - RuntimeError raised with message matching "checkpointer required"
    - generation_status remains at its seeded default ("pending" for a
      freshly-created Application row; the early-exit guard runs before
      the "generating" transition)
    """
    app_row, _, _ = await _seed_application(db_session)
    # Seed an explicit "pending" value so we can assert it is untouched.
    app_row.generation_status = "pending"
    db_session.add(app_row)
    await db_session.commit()

    with pytest.raises(RuntimeError, match="checkpointer required"):
        await generate_materials(app_row.id, db_session, checkpointer=None)

    await db_session.refresh(app_row)
    assert app_row.generation_status == "pending", (
        f"Status should remain 'pending' when the checkpointer guard fires, "
        f"got '{app_row.generation_status}'"
    )
    assert app_row.generation_attempts == 0, (
        "generation_attempts should not be incremented before the guard"
    )

    # No GeneratedDocument rows should have been written
    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    docs = result.scalars().all()
    assert docs == [], f"Expected no documents, got {len(docs)}"
