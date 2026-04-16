"""
Application service — generate_materials() background pipeline.

Called by match_service when a job scores above threshold.
Drives the generation agent and saves resulting documents to DB.
"""

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
            session.add(row)
            saved.append(row)
        else:
            gdoc = GeneratedDocument(
                application_id=app_id,
                doc_type=doc["doc_type"],
                content_md=doc["content_md"],
                generation_model=doc.get("generation_model"),
            )
            session.add(gdoc)
            saved.append(gdoc)
    await session.commit()
    return saved


async def generate_materials(
    application_id: uuid.UUID,
    session: AsyncSession,
    checkpointer=None,
) -> None:
    """
    Background task: generate tailored resume, cover letter, custom answers.
    Updates generation_status on the Application row.
    """
    app = await session.get(Application, application_id)
    if not app:
        await log.awarning("generate_materials.not_found", application_id=str(application_id))
        return

    if app.generation_attempts >= 3:
        await log.awarning(
            "generate_materials.max_attempts", application_id=str(application_id)
        )
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

        # Use generation agent via LangGraph if checkpointer available
        if checkpointer is not None:
            from app.agents.generation_agent import build_graph

            graph = build_graph(checkpointer)
            thread_id = f"gen-{application_id}"
            config = {"configurable": {"thread_id": thread_id}}

            initial_state = {
                "application_id": str(application_id),
                "profile_text": profile_text,
                "job_title": job.title,
                "job_company": job.company_name,
                "job_description": job.description_md or "",
                "base_resume_md": profile.base_resume_md or "",
                "custom_questions": [],
                "documents": [],
                "generation_status": "pending",
                "user_decision": {},
            }

            # Run until interrupt (before review node)
            await graph.ainvoke(initial_state, config)
            # Graph is now paused at review interrupt — docs saved by save_documents_node
        else:
            # Fallback: direct generation without checkpointer (no interrupt/resume)
            await _generate_direct(app, job, profile, profile_text, session)

    except Exception as exc:
        await log.aexception(
            "generate_materials.failed",
            application_id=str(application_id),
            error=str(exc),
        )
        app.generation_status = "failed"
        app.updated_at = datetime.now(UTC)
        session.add(app)
        await session.commit()
        return

    # Mark ready (only if documents were saved by the graph or direct path)
    app.generation_status = "ready"
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()
    await log.ainfo("generate_materials.done", application_id=str(application_id))


async def _generate_direct(
    app: Application,
    job: Job,
    profile: UserProfile,
    profile_text: str,
    session: AsyncSession,
) -> None:
    """
    Direct generation path (no LangGraph checkpointer).
    Used when checkpointer is not available (e.g. unit tests).
    """
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage

    from app.agents.generation_agent import (
        COVER_LETTER_PROMPT,
        RESUME_PROMPT,
        truncate_description,
    )
    from app.config import get_settings

    settings = get_settings()
    llm = ChatAnthropic(
        model=settings.claude_model,
        api_key=settings.anthropic_api_key.get_secret_value(),
    )

    documents = []

    # Resume
    resume_prompt = RESUME_PROMPT.format(
        base_resume_md=(profile.base_resume_md or "")[:6000],
        title=job.title,
        company=job.company_name,
        description=truncate_description(job.description_md or ""),
    )
    resume_result = await llm.ainvoke([HumanMessage(content=resume_prompt)])
    documents.append(
        {
            "doc_type": "tailored_resume",
            "content_md": resume_result.content,
            "generation_model": settings.claude_model,
        }
    )

    # Cover letter
    cl_prompt = COVER_LETTER_PROMPT.format(
        profile_text=profile_text[:3000],
        title=job.title,
        company=job.company_name,
        description=truncate_description(job.description_md or ""),
    )
    cl_result = await llm.ainvoke([HumanMessage(content=cl_prompt)])
    documents.append(
        {
            "doc_type": "cover_letter",
            "content_md": cl_result.content,
            "generation_model": settings.claude_model,
        }
    )

    await save_documents(str(app.id), documents, session)
