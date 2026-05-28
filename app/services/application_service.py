"""
Application service — queue cover-letter generation and run the generation graph.

HTTP requests enqueue work and return 202; app.worker later calls the LLM helper.
No checkpointer, interrupt, or resume endpoint. generation_status:
none -> pending -> generating -> ready/failed.
"""

import time
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user_profile import UserProfile

log = structlog.get_logger()


class IllegalTransition(Exception):
    """Raised when a generation_status transition is not allowed."""


def _generation_dedupe_key(application_id: uuid.UUID) -> str:
    return f"generate-cover-letter:{application_id}"


async def flip_to_pending_and_enqueue(
    *,
    session_factory,
    application_id: uuid.UUID,
) -> int | None:
    from app.worker.queue_service import enqueue

    async with session_factory() as session:
        app = await session.get(Application, application_id)
        if app is None:
            raise ValueError(f"application {application_id} not found")
        if app.generation_status not in {"none", "ready", "failed"}:
            raise IllegalTransition(
                f"cannot request generation from {app.generation_status}"
            )

        app.generation_status = "pending"
        app.updated_at = datetime.now(UTC)
        session.add(app)
        row_id = await enqueue(
            session,
            job_type="generate-cover-letter",
            payload={"application_id": str(application_id)},
            dedupe_key=_generation_dedupe_key(application_id),
        )
        await session.commit()
        return row_id


async def save_documents(
    application_id: str, documents: list[dict], session: AsyncSession
) -> list[GeneratedDocument]:
    """Persist a list of generated document dicts to DB."""
    app_id = uuid.UUID(application_id)
    saved = []
    for doc in documents:
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


async def generate_materials(
    application_id: uuid.UUID,
    session: AsyncSession,
) -> GeneratedDocument:
    """Run the cover-letter graph synchronously and return the saved doc.

    generation_status: generating -> ready (or failed). The single-writer
    rule is preserved here; callers don't touch generation_status directly.
    """
    t0 = time.perf_counter()
    await log.ainfo("generation.started", application_id=str(application_id))

    app = await session.get(Application, application_id)
    if app is None:
        raise ValueError(f"application {application_id} not found")
    app.generation_status = "generating"
    app.generation_attempts += 1
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    try:
        doc_dict = await _generate_materials_doc_dict(application=app, session=session)
        saved = await save_documents(str(application_id), [doc_dict], session)
        content = doc_dict["content_md"]

        app.generation_status = "ready"
        app.cover_letter_content = content
        app.generated_at = datetime.now(UTC)
        app.updated_at = datetime.now(UTC)
        session.add(app)
        await session.commit()

        await log.ainfo(
            "generation.completed",
            application_id=str(application_id),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return saved[0]
    except Exception:
        await log.aexception(
            "generation.failed",
            application_id=str(application_id),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        # Re-read in case another session touched the row.
        fresh = await session.get(Application, application_id)
        if fresh is not None:
            fresh.generation_status = "failed"
            fresh.updated_at = datetime.now(UTC)
            session.add(fresh)
            await session.commit()
        raise


async def generate_materials_llm(application: Application, session: AsyncSession) -> str:
    doc_dict = await _generate_materials_doc_dict(application=application, session=session)
    return doc_dict["content_md"]


async def _generate_materials_doc_dict(
    application: Application, session: AsyncSession
) -> dict:
    job = await session.get(Job, application.job_id)
    profile = await session.get(UserProfile, application.profile_id)
    if job is None or profile is None:
        raise ValueError("missing job or profile")

    from app.agents.generation_agent import build_graph
    from app.services.match_service import format_profile_text
    from app.services.profile_service import get_skills, get_work_experiences

    skills = await get_skills(profile.id, session)
    experiences = await get_work_experiences(profile.id, session)
    profile_text = format_profile_text(profile, skills, experiences)

    initial_state = {
        "application_id": str(application.id),
        "profile_text": profile_text,
        "job_title": job.title,
        "job_company": job.company_name,
        "job_description": job.description or job.description_raw or "",
        "base_resume_md": profile.base_resume_md or "",
        "document": None,
    }

    graph = build_graph()
    result = await graph.ainvoke(initial_state)
    doc_dict = result.get("document")
    if doc_dict is None:
        raise RuntimeError("generation graph returned no document")
    return doc_dict
