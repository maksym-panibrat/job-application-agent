"""Profile management endpoints."""

import hashlib
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile, get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.company import Company
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import match_service, profile_service
from app.services.engagement_service import record_engagement
from app.services.entitlements import (
    CompanyFollowLimitError,
    effective_entitlements,
    get_subscription_snapshot,
    next_search_expiry,
)
from app.services.rate_limit_service import check_daily_quota, check_rate_limit

log = structlog.get_logger()
router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
async def get_profile(
    user: User = Depends(get_current_user),
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    subscription = await get_subscription_snapshot(user.id, session)
    entitlements = effective_entitlements(subscription)
    skills = await profile_service.get_skills(profile.id, session)
    experiences = await profile_service.get_work_experiences(profile.id, session)
    target_companies: list[dict] = []
    if profile.target_company_ids:
        rows = (
            (
                await session.execute(
                    select(Company).where(Company.id.in_(profile.target_company_ids))
                )
            )
            .scalars()
            .all()
        )
        by_id = {c.id: c for c in rows}
        target_companies = [
            {"id": str(by_id[cid].id), "canonical_name": by_id[cid].canonical_name}
            for cid in profile.target_company_ids
            if cid in by_id
        ]
    return {
        "id": str(profile.id),
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "base_resume_md": profile.base_resume_md,
        "target_roles": profile.target_roles,
        "target_locations": profile.target_locations,
        "remote_ok": profile.remote_ok,
        "seniority": profile.seniority,
        "search_keywords": profile.search_keywords,
        "search_active": profile.search_active,
        "search_expires_at": profile.search_expires_at,
        "target_companies": target_companies,
        "subscription": (
            {
                "tier": subscription.tier,
                "status": subscription.status,
                "current_period_end": subscription.current_period_end,
            }
            if subscription is not None
            else None
        ),
        "entitlements": {
            "paid_access": entitlements.paid_access,
            "search_auto_pause": entitlements.search_auto_pause,
        },
        "limits": {
            "followed_companies": entitlements.followed_company_limit,
        },
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "skills": [
            {
                "id": str(s.id),
                "name": s.name,
                "category": s.category,
                "proficiency": s.proficiency,
                "years": s.years,
            }
            for s in skills
        ],
        "work_experiences": [
            {
                "id": str(e.id),
                "company": e.company,
                "title": e.title,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "description_md": e.description_md,
                "technologies": e.technologies,
            }
            for e in experiences
        ],
    }


@router.patch("")
async def update_profile(
    data: dict,
    user: User = Depends(get_current_user),
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if settings.environment == "production":
        # 30/hr — abuse cap, not a usability cap. The previous 5/hr was hostile to
        # real users (typo edits hit it) and reliably broke smoke-prod whenever
        # >2 deploys landed within an hour (smoke makes 2 PATCHes per run).
        await check_rate_limit(
            key=f"profile_edit:{profile.user_id}",
            limit=30,
            window_seconds=3600,
            session=session,
        )
    allowed = {
        "full_name",
        "email",
        "phone",
        "linkedin_url",
        "github_url",
        "portfolio_url",
        "target_roles",
        "target_locations",
        "remote_ok",
        "seniority",
        "search_keywords",
        "target_company_ids",
        "first_name",
        "last_name",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    subscription = await get_subscription_snapshot(user.id, session)
    entitlements = effective_entitlements(subscription)
    try:
        updated = await profile_service.update_profile(
            profile.id,
            filtered,
            session,
            entitlements=entitlements,
            engagement_source="api",
        )
    except CompanyFollowLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": str(updated.id), "updated": True}


@router.post("/upload")
async def upload_resume(
    file: UploadFile = File(...),
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    allowed_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. Use PDF, DOCX, or TXT.",
        )

    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    if settings.environment == "production":
        await check_daily_quota(profile.user_id, "resume_upload", 3, session)

    # Deduplicate by SHA256 against stored raw bytes to skip re-extraction of identical files
    file_sha256 = hashlib.sha256(raw).hexdigest()
    stored_sha256 = (
        hashlib.sha256(profile.base_resume_raw).hexdigest() if profile.base_resume_raw else None
    )
    if stored_sha256 and stored_sha256 == file_sha256:
        return {
            "id": str(profile.id),
            "base_resume_md": profile.base_resume_md,
            "extraction_status": "skipped",
            "message": "Resume unchanged (same file). Skipped re-extraction.",
        }

    updated, extraction_status = await profile_service.save_resume(
        profile.id, file.filename or "resume", raw, session
    )
    await record_engagement(
        session,
        user_id=updated.user_id,
        profile_id=updated.id,
        event_type="resume_uploaded",
        subject_type="profile",
        subject_id=updated.id,
        source="api",
        metadata={"extraction_status": extraction_status},
    )
    await session.commit()
    return {
        "id": str(updated.id),
        "base_resume_md": updated.base_resume_md,
        "extraction_status": extraction_status,
        "message": "Resume uploaded successfully.",
    }


@router.post("/rematch")
async def rematch_profile(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Re-queue all eligible scored applications for re-scoring with the current
    matching prompt. Eligible = status IN (pending_review, auto_rejected) AND
    match_score IS NOT NULL. Dismissed/applied rows are user decisions and stay.

    Use after a prompt iteration or scoring-rubric change. The worker queue
    drains enqueued match jobs on its next tick."""
    if settings.environment == "production":
        # 6/hr — re-scoring N applications consumes N LLM calls and is the
        # most expensive profile-scoped action. Lower than profile_edit (30/hr).
        await check_rate_limit(
            key=f"profile_rematch:{profile.user_id}",
            limit=6,
            window_seconds=3600,
            session=session,
        )
    reset = await match_service.mark_for_rescore(profile.id, session)
    await log.ainfo("profile.rematch", profile_id=str(profile.id), reset=reset)
    return {"reset": reset}


@router.patch("/search")
async def toggle_search(
    data: dict,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Resume or pause job search. POST {search_active: true} to resume."""
    search_active = data.get("search_active", True)
    was_search_active = bool(profile.search_active)
    updates: dict = {"search_active": search_active}
    if search_active:
        updates["search_expires_at"] = next_search_expiry(datetime.now(UTC), settings)
    else:
        updates["search_expires_at"] = None
    updated = await profile_service.update_profile(profile.id, updates, session)
    if search_active and not was_search_active:
        await record_engagement(
            session,
            user_id=updated.user_id,
            profile_id=updated.id,
            event_type="search_resumed",
            subject_type="profile",
            subject_id=updated.id,
            source="api",
        )
        await session.commit()
    return {
        "search_active": updated.search_active,
        "search_expires_at": updated.search_expires_at,
    }
