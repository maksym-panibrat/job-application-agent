"""
Application service — generate_materials() background pipeline.

Called by match_service when a job scores above threshold.
Drives the generation agent and saves resulting documents to DB.

Lifecycle:
    pending -> generating -> awaiting_review (interrupt) -> ready (after approve)
                                           \
                                            -> regenerate -> generating -> awaiting_review ...
"""

import time
import uuid
from datetime import UTC, datetime

import structlog
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user_profile import UserProfile

log = structlog.get_logger()


async def save_documents(
    application_id: str, documents: list[dict], session: AsyncSession
) -> list[GeneratedDocument]:
    """Persist a list of generated document dicts to DB."""
    import uuid as _uuid

    app_id = _uuid.UUID(application_id)
    saved = []
    for doc in documents:
        # Check for existing document of this type (avoid duplicates on retry)
        existing = await session.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.application_id == app_id,
                GeneratedDocument.doc_type == doc["doc_type"],
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.content_md = doc["content_md"]
            row.generation_model = doc.get("generation_model")
            row.structured_content = doc.get("structured_content")
            session.add(row)
            saved.append(row)
        else:
            gdoc = GeneratedDocument(
                application_id=app_id,
                doc_type=doc["doc_type"],
                content_md=doc["content_md"],
                generation_model=doc.get("generation_model"),
                structured_content=doc.get("structured_content"),
            )
            session.add(gdoc)
            saved.append(gdoc)
    await session.commit()
    return saved


def _status_from_graph_next(next_nodes: tuple) -> str:
    """Map LangGraph state.next to an application generation_status.

    - Empty tuple = graph reached END -> "ready".
    - Non-empty = graph paused at an interrupt (before the 'review' node in
      our generation graph) -> "awaiting_review".
    """
    return "ready" if next_nodes == () else "awaiting_review"


async def generate_materials(
    application_id: uuid.UUID,
    session: AsyncSession,
    checkpointer=None,
) -> None:
    """
    Background task: generate tailored resume, cover letter, custom answers.
    Updates generation_status on the Application row.

    A LangGraph checkpointer is required — there is no direct-generation
    fallback. Callers should resolve the checkpointer from
    ``request.app.state.checkpointer`` (initialized in the FastAPI lifespan).

    After graph.ainvoke() returns, inspect the graph state: if paused at the
    review interrupt, set ``awaiting_review``; if the graph reached END, set
    ``ready``.
    """
    if checkpointer is None:
        raise RuntimeError(
            "checkpointer required — generate_materials cannot run without a LangGraph checkpointer"
        )
    t0 = time.perf_counter()
    await log.ainfo("generation.started", application_id=str(application_id))
    app = await session.get(Application, application_id)
    if not app:
        await log.awarning("generate_materials.not_found", application_id=str(application_id))
        return

    if app.generation_attempts >= 3:
        await log.awarning("generate_materials.max_attempts", application_id=str(application_id))
        return

    # Mark as generating
    app.generation_status = "generating"
    app.generation_attempts += 1
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    try:
        job = await session.get(Job, app.job_id)
        profile = await session.get(UserProfile, app.profile_id)
        if not job or not profile:
            raise ValueError("Job or profile not found")

        from app.services.match_service import format_profile_text
        from app.services.profile_service import get_skills, get_work_experiences

        skills = await get_skills(profile.id, session)
        experiences = await get_work_experiences(profile.id, session)
        profile_text = format_profile_text(profile, skills, experiences)

        # Use generation agent via LangGraph
        from app.agents.generation_agent import build_graph

        graph = build_graph(checkpointer)
        thread_id = f"gen-{application_id}"
        config = {"configurable": {"thread_id": thread_id}}

        custom_questions: list = []
        if job.ats_type == "greenhouse" and job.supports_api_apply and job.apply_url:
            from app.sources.greenhouse import (
                GreenhouseUnavailable,
                get_job_questions_by_url,
            )

            try:
                custom_questions = await get_job_questions_by_url(job.apply_url)
            except GreenhouseUnavailable as exc:
                await log.awarning(
                    "generation.greenhouse_questions_unavailable",
                    application_id=str(application_id),
                    apply_url=job.apply_url,
                    error=str(exc),
                )
                custom_questions = []

        initial_state = {
            "application_id": str(application_id),
            "profile_text": profile_text,
            "job_title": job.title,
            "job_company": job.company_name,
            "job_description": job.description_md or "",
            "base_resume_md": profile.base_resume_md or "",
            "custom_questions": custom_questions,
            "documents": [],
            "generation_status": "pending",
            "user_decision": {},
        }

        # Run until interrupt (before review node)
        await graph.ainvoke(initial_state, config)
        # Inspect where the graph landed — either paused at 'review' (docs saved
        # by save_documents_node, awaiting user decision) or at END.
        state = await graph.aget_state(config)
        next_status = _status_from_graph_next(state.next)

    except Exception as exc:
        await log.aexception(
            "generation.failed",
            application_id=str(application_id),
            error=str(exc),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        app.generation_status = "failed"
        app.updated_at = datetime.now(UTC)
        session.add(app)
        await session.commit()
        return

    app.generation_status = next_status
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()
    await log.ainfo(
        "generation.completed",
        application_id=str(application_id),
        status=app.generation_status,
        graph_next=list(state.next),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )


async def resume_generation(
    application_id: uuid.UUID,
    decision: dict,
    session: AsyncSession,
    checkpointer=None,
) -> None:
    """
    Resume a paused generation graph with the user's review decision.

    ``decision`` is the payload passed to ``Command(resume=...)``:
      - ``{"approved": True}``  -> graph proceeds to finalize, ends -> "ready"
      - ``{"regenerate": True}`` -> graph loops back to load_context, generates
        new docs, and pauses at the next review interrupt -> "awaiting_review"

    After the resume invoke, the new status is derived from ``state.next``.
    """
    if checkpointer is None:
        raise RuntimeError(
            "checkpointer required — resume_generation cannot run without a LangGraph checkpointer"
        )

    t0 = time.perf_counter()
    # Tag the decision as a scalar for observability — never log the raw
    # dict. A future caller could route arbitrary keys through ``decision``;
    # a tag keeps structlog free of PII / unbounded payloads.
    if decision.get("approved"):
        decision_type = "approve"
    elif decision.get("regenerate"):
        decision_type = "regenerate"
    else:
        decision_type = "unknown"
    await log.ainfo(
        "generation.resumed",
        application_id=str(application_id),
        decision_type=decision_type,
    )

    app = await session.get(Application, application_id)
    if not app:
        await log.awarning("resume_generation.not_found", application_id=str(application_id))
        return

    try:
        from app.agents.generation_agent import build_graph

        graph = build_graph(checkpointer)
        thread_id = f"gen-{application_id}"
        config = {"configurable": {"thread_id": thread_id}}

        await graph.ainvoke(Command(resume=decision), config)

        state = await graph.aget_state(config)
        next_status = _status_from_graph_next(state.next)

    except Exception as exc:
        await log.aexception(
            "generation.resume_failed",
            application_id=str(application_id),
            error=str(exc),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        # Re-read fresh — the original snapshot may be stale if the graph
        # mutated the row from another session (e.g. the API-layer atomic
        # UPDATE that flipped 'awaiting_review' -> 'generating' before
        # scheduling this task).
        fresh = await session.get(Application, application_id)
        if fresh is not None:
            fresh.generation_status = "failed"
            fresh.updated_at = datetime.now(UTC)
            session.add(fresh)
            await session.commit()
        return

    # Re-read to pick up any out-of-session writes (the API layer's atomic
    # UPDATE bumped generation_attempts and flipped status to 'generating'
    # between scheduling this task and us running; the snapshot on ``app``
    # above predates that).
    fresh = await session.get(Application, application_id)
    if fresh is None:
        await log.awarning("resume_generation.row_vanished", application_id=str(application_id))
        return
    fresh.generation_status = next_status
    fresh.updated_at = datetime.now(UTC)
    session.add(fresh)
    await session.commit()

    outcome = "approved" if next_status == "ready" else "regenerated"
    await log.ainfo(
        "generation.resume_completed",
        application_id=str(application_id),
        status=fresh.generation_status,
        outcome=outcome,
        graph_next=list(state.next),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
