"""Applications endpoints — list, review, generate, status stream."""

import asyncio
import json
import uuid
from datetime import UTC

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services import match_service

log = structlog.get_logger()
router = APIRouter(prefix="/api/applications", tags=["applications"])


async def _generate_in_background(app_id: uuid.UUID, checkpointer) -> None:
    """Background task: generate materials with its own DB session."""
    from app.database import get_session_factory
    from app.services.application_service import generate_materials

    factory = get_session_factory()
    async with factory() as session:
        await generate_materials(app_id, session, checkpointer=checkpointer)


@router.get("")
async def list_applications(
    status: str | None = None,
    min_score: float | None = None,
    limit: int = 20,
    offset: int = 0,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    rows = await match_service.list_applications(
        profile.id, session, status=status, min_score=min_score, limit=limit, offset=offset
    )

    result = []
    for app, job in rows:
        result.append(
            {
                "id": str(app.id),
                "status": app.status,
                "generation_status": app.generation_status,
                "match_score": app.match_score,
                "match_rationale": app.match_rationale,
                "match_strengths": app.match_strengths,
                "match_gaps": app.match_gaps,
                "created_at": app.created_at,
                "job": {
                    "id": str(job.id),
                    "title": job.title,
                    "company_name": job.company_name,
                    "location": job.location,
                    "workplace_type": job.workplace_type,
                    "salary": job.salary,
                    "contract_type": job.contract_type,
                    "apply_url": job.apply_url,
                    "ats_type": job.ats_type,
                    "supports_api_apply": job.supports_api_apply,
                    "posted_at": job.posted_at,
                },
            }
        )
    return result


@router.get("/{app_id}")
async def get_application(
    app_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    import uuid

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    job = await session.get(Job, app.job_id)
    docs_result = await session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app.id)
    )
    docs = docs_result.scalars().all()

    return {
        "id": str(app.id),
        "status": app.status,
        "generation_status": app.generation_status,
        "generation_attempts": app.generation_attempts,
        "match_score": app.match_score,
        "match_rationale": app.match_rationale,
        "match_strengths": app.match_strengths,
        "match_gaps": app.match_gaps,
        "created_at": app.created_at,
        "job": {
            "id": str(job.id),
            "title": job.title,
            "company_name": job.company_name,
            "location": job.location,
            "workplace_type": job.workplace_type,
            "salary": job.salary,
            "contract_type": job.contract_type,
            "description_md": job.description_md,
            "apply_url": job.apply_url,
            "ats_type": job.ats_type,
            "supports_api_apply": job.supports_api_apply,
            "posted_at": job.posted_at,
        }
        if job
        else None,
        "documents": [
            {
                "id": str(d.id),
                "doc_type": d.doc_type,
                "content_md": d.user_edited_md or d.content_md,
                "has_edits": d.user_edited_md is not None,
                "generation_model": d.generation_model,
                "created_at": d.created_at,
            }
            for d in docs
        ],
    }


@router.patch("/{app_id}")
async def review_application(
    app_id: str,
    data: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Action: approved, dismissed, applied.
    Approving also triggers immediate document generation."""
    from datetime import datetime

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    action = data.get("status")
    if action not in ("approved", "dismissed", "applied"):
        raise HTTPException(
            status_code=400, detail="status must be approved, dismissed, or applied"
        )

    app.status = action
    if action == "approved" and app.generation_status in ("none", "failed"):
        app.generation_status = "pending"
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    if action == "approved" and app.generation_status == "pending":
        checkpointer = getattr(request.app.state, "checkpointer", None)
        background_tasks.add_task(_generate_in_background, uuid.UUID(app_id), checkpointer)

    return {"id": str(app.id), "status": app.status, "generation_status": app.generation_status}


@router.patch("/{app_id}/documents/{doc_id}")
async def update_document(
    app_id: str,
    doc_id: str,
    data: dict,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Save user edits to a generated document."""
    import uuid

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    doc = await session.get(GeneratedDocument, uuid.UUID(doc_id))
    if not doc or doc.application_id != app.id:
        raise HTTPException(status_code=404, detail="Document not found")

    user_edited = data.get("user_edited_md")
    if user_edited is not None:
        doc.user_edited_md = user_edited
        session.add(doc)
        await session.commit()

    return {"id": str(doc.id), "saved": True}


@router.get("/{app_id}/status/stream")
async def stream_generation_status(
    app_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """SSE endpoint — polls generation_status until ready or failed."""
    import uuid

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    async def event_stream():
        from app.database import get_session_factory
        factory = get_session_factory()
        for _ in range(60):  # max 5 minutes
            async with factory() as s:
                a = await s.get(Application, uuid.UUID(app_id))
                status = a.generation_status if a else "failed"
            yield f"data: {json.dumps({'generation_status': status})}\n\n"
            if status in ("ready", "failed"):
                return
            await asyncio.sleep(5)
        yield f"data: {json.dumps({'generation_status': 'timeout'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{app_id}/regenerate")
async def regenerate_application(
    app_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Reset generation_status to pending and trigger generation immediately."""
    from datetime import datetime

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    if app.generation_attempts >= 3:
        raise HTTPException(status_code=429, detail="Max generation attempts (3) reached")

    app.generation_status = "pending"
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    checkpointer = getattr(request.app.state, "checkpointer", None)
    background_tasks.add_task(_generate_in_background, uuid.UUID(app_id), checkpointer)

    return {"id": str(app.id), "generation_status": "pending"}


@router.post("/{app_id}/submit")
async def submit_application(
    app_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """
    Attempt ATS API submission (Greenhouse only).
    Lever/Ashby fall back to method=manual (open apply URL in browser).
    """
    import uuid
    from datetime import datetime

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    job = await session.get(Job, app.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.supports_api_apply:
        # Lever/Ashby: tell frontend to open apply URL
        return {"method": "manual", "apply_url": job.apply_url}

    # Get tailored resume and cover letter from generated documents
    docs_result = await session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app.id)
    )
    docs = {d.doc_type: d for d in docs_result.scalars().all()}

    resume_doc = docs.get("tailored_resume")
    cover_letter_doc = docs.get("cover_letter")

    resume_md = (resume_doc.user_edited_md or resume_doc.content_md) if resume_doc else None
    cover_letter_md = (
        (cover_letter_doc.user_edited_md or cover_letter_doc.content_md)
        if cover_letter_doc
        else None
    )

    # Parse name from profile
    name_parts = (profile.full_name or "").split(maxsplit=1)
    first_name = name_parts[0] if name_parts else "Candidate"
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    from app.sources.greenhouse import try_submit

    result = await try_submit(
        apply_url=job.apply_url,
        first_name=first_name,
        last_name=last_name,
        email=profile.email or "",
        phone=profile.phone,
        resume_md=resume_md,
        cover_letter_md=cover_letter_md,
    )

    if result.get("success"):
        app.status = "applied"
        app.updated_at = datetime.now(UTC)
        session.add(app)
        await session.commit()

    return result
