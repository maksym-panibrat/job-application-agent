"""Applications endpoints — list, review, generate, status stream."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
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


async def _mark_generation_failed(app_id: uuid.UUID, failure_event: str) -> None:
    """Open a fresh session and flip ``generating`` -> ``failed``.

    Used as a last-resort recovery path when a background task crashes before
    the service layer's own try/except can set ``failed`` itself. We only
    touch the row when it is still ``generating`` to avoid clobbering a
    terminal state written elsewhere.
    """
    from app.database import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as recovery_session:
            row = await recovery_session.get(Application, app_id)
            if row is not None and row.generation_status == "generating":
                row.generation_status = "failed"
                row.updated_at = datetime.now(UTC)
                recovery_session.add(row)
                await recovery_session.commit()
    except Exception:
        await log.aexception(failure_event, application_id=str(app_id))


async def _generate_in_background(app_id: uuid.UUID, checkpointer) -> None:
    """Background task: generate materials with its own DB session.

    Wraps ``generate_materials`` in a fail-safe recovery block so a crash in
    session acquisition / imports / the service call itself cannot leave the
    row pinned in ``generating`` forever.
    """
    from app.database import get_session_factory
    from app.services.application_service import generate_materials

    try:
        factory = get_session_factory()
        async with factory() as session:
            await generate_materials(app_id, session, checkpointer=checkpointer)
    except Exception:
        await log.aexception("generate.background_crash", application_id=str(app_id))
        await _mark_generation_failed(app_id, "generate.background_recovery_failed")


async def _resume_in_background(app_id: uuid.UUID, decision: dict, checkpointer) -> None:
    """Background task: resume a paused generation graph with its own DB session.

    Wraps ``resume_generation`` in a fail-safe recovery block: if anything at
    all (session acquisition, imports, the service call itself) raises before
    ``resume_generation``'s internal try/except sets status to ``failed``,
    we open a fresh session and flip ``generating`` -> ``failed`` so the row
    never stays pinned in ``generating``.
    """
    from app.database import get_session_factory
    from app.services.application_service import resume_generation

    try:
        factory = get_session_factory()
        async with factory() as session:
            await resume_generation(app_id, decision, session, checkpointer=checkpointer)
    except Exception:
        await log.aexception("resume.background_crash", application_id=str(app_id))
        await _mark_generation_failed(app_id, "resume.background_recovery_failed")


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
    request: Request,
    background_tasks: BackgroundTasks,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Action: approved, dismissed, applied.
    Approving also triggers immediate document generation."""
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

    structured = data.get("structured_content")
    if structured is not None:
        doc.structured_content = structured

    if user_edited is not None or structured is not None:
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
        # Poll the DB for up to 5 minutes (60 × 5s). If you bump this, also bump
        # GENERATION_POLL_TIMEOUT_MS in frontend/src/pages/ApplicationReview.tsx
        # (frontend must give up AFTER the server does — currently backend + 30s).
        for _ in range(60):
            async with factory() as s:
                a = await s.get(Application, uuid.UUID(app_id))
                status = a.generation_status if a else "failed"
            yield f"data: {json.dumps({'generation_status': status})}\n\n"
            # Terminal states: the graph is either done ("ready"/"failed") or
            # paused at the review interrupt ("awaiting_review"). In all cases
            # there is nothing more to poll for — the UI drives the next step.
            if status in ("ready", "failed", "awaiting_review"):
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


_RESUME_DECISION_MAP: dict[str, dict] = {
    "approve": {"approved": True},
    "regenerate": {"regenerate": True},
}


@router.post("/{app_id}/resume")
async def resume_application(
    app_id: str,
    data: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Resume a paused generation graph with the user's review decision.

    Body: ``{"decision": "approve"}`` or ``{"decision": "regenerate"}``.

    Only valid when generation_status == 'awaiting_review'. The graph is
    resumed in a background task so this endpoint returns quickly; the UI
    polls the status stream to see the transition to 'ready' or the next
    'awaiting_review'.
    """
    decision = data.get("decision")
    if decision not in _RESUME_DECISION_MAP:
        raise HTTPException(
            status_code=422,
            detail="decision must be 'approve' or 'regenerate'",
        )

    app_uuid = uuid.UUID(app_id)

    # 404 / ownership check first — read a snapshot to verify the row exists
    # and belongs to the caller. The actual status transition is guarded
    # atomically below to prevent TOCTOU races between concurrent resume POSTs.
    app = await session.get(Application, app_uuid)
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="checkpointer not initialized")

    # Atomic conditional UPDATE: flip awaiting_review -> generating in a single
    # statement so two concurrent POSTs cannot both pass the guard. The
    # regenerate path additionally enforces the 3-attempt cap and bumps the
    # counter atomically, so a caller cannot bypass the cap by racing.
    if decision == "regenerate":
        result = await session.execute(
            text(
                "UPDATE applications "
                "SET generation_status = 'generating', "
                "    generation_attempts = generation_attempts + 1, "
                "    updated_at = NOW() "
                "WHERE id = :id "
                "  AND generation_status = 'awaiting_review' "
                "  AND generation_attempts < 3 "
                "RETURNING generation_attempts"
            ),
            {"id": app_uuid},
        )
        row = result.fetchone()
        if row is None:
            # Disambiguate 409 (wrong status) vs 429 (attempts maxed)
            await session.rollback()
            latest = await session.get(Application, app_uuid)
            if latest is not None and latest.generation_attempts >= 3:
                raise HTTPException(status_code=429, detail="Max generation attempts (3) reached")
            current = latest.generation_status if latest else "unknown"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Application is not awaiting review (current generation_status="
                    f"'{current}'); resume is only valid after the graph pauses."
                ),
            )
    else:
        result = await session.execute(
            text(
                "UPDATE applications "
                "SET generation_status = 'generating', updated_at = NOW() "
                "WHERE id = :id AND generation_status = 'awaiting_review' "
                "RETURNING id"
            ),
            {"id": app_uuid},
        )
        if result.fetchone() is None:
            await session.rollback()
            latest = await session.get(Application, app_uuid)
            current = latest.generation_status if latest else "unknown"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Application is not awaiting review (current generation_status="
                    f"'{current}'); resume is only valid after the graph pauses."
                ),
            )
    await session.commit()

    command_payload = _RESUME_DECISION_MAP[decision]
    background_tasks.add_task(_resume_in_background, app_uuid, command_payload, checkpointer)

    return {
        "id": str(app.id),
        "generation_status": "generating",
        "decision": decision,
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
    import uuid as _uuid

    app = await session.get(Application, _uuid.UUID(app_id))
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
