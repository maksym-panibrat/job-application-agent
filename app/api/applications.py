"""Applications endpoints — list, review, generate cover letter, mark applied."""

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
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
                "match_summary": app.match_summary,
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
        "match_summary": app.match_summary,
        "match_rationale": app.match_rationale,
        "match_strengths": app.match_strengths,
        "match_gaps": app.match_gaps,
        "applied_at": app.applied_at,
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
            "posted_at": job.posted_at,
        }
        if job
        else None,
        "documents": [
            {
                "id": str(d.id),
                "doc_type": d.doc_type,
                "content_md": d.user_edited_md or d.content_md,
                "structured_content": d.structured_content,
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
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Status transitions only: dismissed, applied. (Cover-letter generation
    is a separate endpoint, /cover-letter.)"""
    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    action = data.get("status")
    if action not in ("dismissed", "applied"):
        raise HTTPException(status_code=400, detail="status must be dismissed or applied")

    if action == "applied" and app.status != "applied":
        app.applied_at = datetime.now(UTC)
    app.status = action
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    return {"id": str(app.id), "status": app.status}


@router.patch("/{app_id}/documents/{doc_id}")
async def update_document(
    app_id: str,
    doc_id: str,
    data: dict,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Save user edits to a generated document."""
    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    doc = await session.get(GeneratedDocument, uuid.UUID(doc_id))
    if not doc or doc.application_id != app.id:
        raise HTTPException(status_code=404, detail="Document not found")

    user_edited = data.get("user_edited_md")
    if user_edited is not None:
        doc.user_edited_md = user_edited

    structured = data.get("structured_content")
    if structured is not None:
        doc.structured_content = structured

    if user_edited is not None or structured is not None:
        session.add(doc)
        await session.commit()

    return {"id": str(doc.id), "saved": True}


@router.post("/{app_id}/cover-letter")
async def generate_cover_letter(
    app_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Synchronously generate a cover letter for the application.

    Returns the saved GeneratedDocument. Generation runs in-process — the
    request blocks until the LLM responds (≈10-30s). Re-invoking on an
    already-generated app overwrites the existing cover letter.
    """
    from app.services.application_service import generate_materials

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.generation_status == "generating":
        raise HTTPException(status_code=409, detail="Generation already in progress")

    doc = await generate_materials(app.id, session)
    return {
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "content_md": doc.content_md,
        "generation_model": doc.generation_model,
        "created_at": doc.created_at,
    }


@router.post("/{app_id}/mark-applied")
async def mark_applied(
    app_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Manually mark an application as applied.

    Transitions status from pending_review or open to applied and records
    the applied_at timestamp. Idempotent — calling again when already applied
    returns the existing applied_at without modifying it.
    """
    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status == "applied":
        return {"id": str(app.id), "status": app.status, "applied_at": app.applied_at}
    if app.status not in ("pending_review", "open"):
        raise HTTPException(status_code=409, detail=f"Cannot mark applied from status {app.status}")
    app.status = "applied"
    app.applied_at = datetime.now(UTC)
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()
    return {"id": str(app.id), "status": app.status, "applied_at": app.applied_at}
