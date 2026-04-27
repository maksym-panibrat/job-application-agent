"""
Application service — generate_materials() drives the cover-letter agent.

Synchronous: callers (the API route) await this directly. No checkpointer,
no interrupt, no resume. generation_status: none -> generating -> ready/failed.
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
    job = await session.get(Job, app.job_id)
    profile = await session.get(UserProfile, app.profile_id)
    if job is None or profile is None:
        raise ValueError("missing job or profile")

    app.generation_status = "generating"
    app.generation_attempts += 1
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    try:
        from app.agents.generation_agent import build_graph
        from app.services.match_service import format_profile_text
        from app.services.profile_service import get_skills, get_work_experiences

        skills = await get_skills(profile.id, session)
        experiences = await get_work_experiences(profile.id, session)
        profile_text = format_profile_text(profile, skills, experiences)

        initial_state = {
            "application_id": str(application_id),
            "profile_text": profile_text,
            "job_title": job.title,
            "job_company": job.company_name,
            "job_description": job.description_md or "",
            "base_resume_md": profile.base_resume_md or "",
            "document": None,
        }

        graph = build_graph()
        result = await graph.ainvoke(initial_state)
        doc_dict = result.get("document")
        if doc_dict is None:
            raise RuntimeError("generation graph returned no document")

        saved = await save_documents(str(application_id), [doc_dict], session)

        app.generation_status = "ready"
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
