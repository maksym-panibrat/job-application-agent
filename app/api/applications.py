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


async def _resume_in_background(app_id: uuid.UUID, decision: dict, checkpointer) -> None:
    """Background task: resume a paused generation graph with its own DB session."""
    from app.database import get_session_factory
    from app.services.application_service import resume_generation

    factory = get_session_factory()
    async with factory() as session:
        await resume_generation(app_id, decision, session, checkpointer=checkpointer)


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
                "user_interest": app.user_interest,
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
        "submitted_at": app.submitted_at,
        "submission_method": app.submission_method,
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


@router.patch("/{app_id}/interest")
async def set_interest(
    app_id: uuid.UUID,
    data: dict,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Mark a match as interested or not-interested without opening full review."""
    interest = data.get("interest")  # "interested", "not_interested", or None
    if interest not in ("interested", "not_interested", None):
        raise HTTPException(
            status_code=422,
            detail="interest must be 'interested', 'not_interested', or null",
        )

    app = await session.get(Application, app_id)
    if app is None or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    app.user_interest = interest
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return {"id": str(app.id), "user_interest": app.user_interest}


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
    from datetime import datetime

    decision = data.get("decision")
    if decision not in _RESUME_DECISION_MAP:
        raise HTTPException(
            status_code=422,
            detail="decision must be 'approve' or 'regenerate'",
        )

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    if app.generation_status != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Application is not awaiting review (current generation_status="
                f"'{app.generation_status}'); resume is only valid after the graph pauses."
            ),
        )

    # Regenerate path re-enters load_context and will bump generation_attempts
    # when the graph loops — but the model-level check in generate_materials is
    # bypassed during resume, so enforce it here too.
    if decision == "regenerate" and app.generation_attempts >= 3:
        raise HTTPException(status_code=429, detail="Max generation attempts (3) reached")

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="checkpointer not initialized")

    # Flip to "generating" before scheduling so the UI sees the transition
    # immediately (the status-stream poll is what watches for the next state).
    app.generation_status = "generating"
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    command_payload = _RESUME_DECISION_MAP[decision]
    background_tasks.add_task(
        _resume_in_background, uuid.UUID(app_id), command_payload, checkpointer
    )

    return {
        "id": str(app.id),
        "generation_status": "generating",
        "decision": decision,
    }


SMOKE_USER_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@router.post("/{app_id}/submit")
async def submit_application(
    app_id: str,
    request: Request,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """
    Attempt ATS API submission (Greenhouse or Lever).
    Other ATS types fall back to method=manual (open apply URL in browser).

    HTTP status codes:
      200 — success, manual fallback, needs_review, or dry-run
      400 — ATS rejected the submission (4xx upstream)
      502 — ATS server error (5xx upstream) or network/timeout failure
    """
    from datetime import datetime

    app = await session.get(Application, uuid.UUID(app_id))
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    job = await session.get(Job, app.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Dry-run gating: only honored for the smoke user; silently ignored otherwise
    dry_run = False
    if request.headers.get("x-smoke-dryrun", "").lower() == "true":
        if profile.user_id == SMOKE_USER_ID:
            dry_run = True

    # Load all generated documents
    docs_result = await session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app.id)
    )
    docs = {d.doc_type: d for d in docs_result.scalars().all()}

    # Check custom_answers for unanswered required questions
    custom_answers_doc = docs.get("custom_answers")
    custom_answers: dict[str, str] | None = None
    if custom_answers_doc and custom_answers_doc.structured_content:
        custom_answers = custom_answers_doc.structured_content
        unanswered = [label for label, answer in custom_answers.items() if not answer]
        if unanswered:
            return {"method": "needs_review", "unanswered_questions": unanswered}

    resume_doc = docs.get("tailored_resume")
    cover_letter_doc = docs.get("cover_letter")

    resume_md = (resume_doc.user_edited_md or resume_doc.content_md) if resume_doc else None
    cover_letter_md = (
        (cover_letter_doc.user_edited_md or cover_letter_doc.content_md)
        if cover_letter_doc
        else None
    )

    # Resolve first/last name from profile
    if profile.first_name:
        first_name = profile.first_name
        last_name = profile.last_name or ""
    else:
        name_parts = (profile.full_name or "").split(maxsplit=1)
        first_name = name_parts[0] if name_parts else "Candidate"
        last_name = name_parts[1] if len(name_parts) > 1 else ""

    ats_type = job.ats_type or ""

    # Short-circuit for smoke dry-run: write audit fields without hitting the ATS
    if dry_run:
        result: dict = {
            "method": "dry_run",
            "would_submit": True,
            "ats_type": ats_type if ats_type else "manual",
        }
        app.submitted_at = datetime.now(UTC)
        app.submission_method = "dry_run"
        app.submission_result = result
        app.updated_at = datetime.now(UTC)
        session.add(app)
        await session.commit()
        return result

    if ats_type == "greenhouse" and job.supports_api_apply:
        from app.sources.greenhouse import try_submit as greenhouse_submit

        result = await greenhouse_submit(
            apply_url=job.apply_url,
            first_name=first_name,
            last_name=last_name,
            email=profile.email or "",
            phone=profile.phone,
            resume_md=resume_md,
            cover_letter_md=cover_letter_md,
            custom_answers=custom_answers,
        )
    elif ats_type == "lever":
        from app.sources.lever_submit import try_submit as lever_submit

        result = await lever_submit(
            apply_url=job.apply_url,
            resume_text=resume_md or "",
            cover_letter_md=cover_letter_md or "",
            first_name=first_name,
            last_name=last_name,
            email=profile.email or "",
            api_key=None,
        )
    else:
        result = {"method": "manual", "apply_url": job.apply_url}

    # Record audit fields for all submission attempts (needs_review already returned early)
    app.submitted_at = datetime.now(UTC)
    app.submission_method = result["method"]
    app.submission_result = result
    if result.get("success"):
        app.status = "applied"
    app.updated_at = datetime.now(UTC)
    session.add(app)
    await session.commit()

    # Map ATS result to an appropriate HTTP status code
    result_method = result.get("method", "")
    if result_method in ("manual", "needs_review"):
        http_status = 200
    elif result.get("success"):
        http_status = 200
    elif result.get("status_code") is None:
        # Network error / timeout / exception — upstream was unreachable
        http_status = 502
        result.setdefault("failure_reason", "ats_unreachable")
    elif 400 <= result["status_code"] < 500:
        http_status = 400
        result.setdefault("failure_reason", f"ats_rejected_{result['status_code']}")
    elif 500 <= result["status_code"] < 600:
        http_status = 502
        result.setdefault("failure_reason", f"ats_upstream_error_{result['status_code']}")
    else:
        http_status = 200

    from fastapi.responses import JSONResponse

    return JSONResponse(content=result, status_code=http_status)
