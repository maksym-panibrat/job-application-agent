"""Profile management endpoints."""

import hashlib
from datetime import UTC

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user_profile import UserProfile
from app.services import profile_service
from app.services.rate_limit_service import check_daily_quota, check_rate_limit

log = structlog.get_logger()
router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
async def get_profile(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    skills = await profile_service.get_skills(profile.id, session)
    experiences = await profile_service.get_work_experiences(profile.id, session)
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
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if settings.environment == "production":
        await check_rate_limit(
            key=f"profile_edit:{profile.user_id}",
            limit=5,
            window_seconds=3600,
            session=session,
        )
    allowed = {
        "full_name", "email", "phone", "linkedin_url", "github_url", "portfolio_url",
        "target_roles", "target_locations", "remote_ok", "seniority", "search_keywords",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    updated = await profile_service.update_profile(profile.id, filtered, session)
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
        hashlib.sha256(profile.base_resume_raw).hexdigest()
        if profile.base_resume_raw
        else None
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
    return {
        "id": str(updated.id),
        "base_resume_md": updated.base_resume_md,
        "extraction_status": extraction_status,
        "message": "Resume uploaded successfully.",
    }


@router.patch("/search")
async def toggle_search(
    data: dict,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Resume or pause job search. POST {search_active: true} to resume."""
    from datetime import datetime, timedelta

    search_active = data.get("search_active", True)
    updates: dict = {"search_active": search_active}
    if search_active:
        from app.config import get_settings
        settings = get_settings()
        updates["search_expires_at"] = datetime.now(UTC) + timedelta(
            days=settings.search_auto_pause_days
        )
    updated = await profile_service.update_profile(profile.id, updates, session)
    return {
        "search_active": updated.search_active,
        "search_expires_at": updated.search_expires_at,
    }
