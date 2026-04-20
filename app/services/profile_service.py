import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.user_profile import Skill, UserProfile, WorkExperience
from app.services.resume_extraction import extract_profile_from_resume
from app.sources.resume_parser import parse_resume

log = structlog.get_logger()


async def get_profile_by_user(user_id: uuid.UUID, session: AsyncSession) -> UserProfile | None:
    result = await session.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_profile(user_id: uuid.UUID, session: AsyncSession) -> UserProfile:
    profile = await get_profile_by_user(user_id, session)
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


async def update_profile(
    profile_id: uuid.UUID, data: dict, session: AsyncSession
) -> UserProfile:
    profile = await session.get(UserProfile, profile_id)
    for key, value in data.items():
        if hasattr(profile, key) and value is not None:
            setattr(profile, key, value)
    profile.updated_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def save_resume(
    profile_id: uuid.UUID, filename: str, raw_bytes: bytes, session: AsyncSession
) -> UserProfile:
    profile = await session.get(UserProfile, profile_id)
    md = parse_resume(filename, raw_bytes)
    profile.base_resume_raw = raw_bytes
    profile.base_resume_md = md
    profile.updated_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    # Extract structured profile data from resume using LLM (best-effort)
    if md:
        extracted = await extract_profile_from_resume(md)
        if extracted:
            await _apply_extracted_resume_data(profile_id, extracted, session)
            await session.refresh(profile)

    return profile


async def _apply_extracted_resume_data(
    profile_id: uuid.UUID, data: dict, session: AsyncSession
) -> None:
    """Apply LLM-extracted resume data to the profile, skills, and work_experiences."""
    SCALAR_FIELDS = {
        "full_name", "email", "phone", "linkedin_url",
        "github_url", "portfolio_url", "target_roles",
    }
    skills = list(data.pop("skills", None) or [])
    experiences = list(data.pop("work_experiences", None) or [])
    flat = {k: v for k, v in data.items() if k in SCALAR_FIELDS and v}

    if flat:
        try:
            await update_profile(profile_id, flat, session)
        except Exception as exc:
            await log.awarning("resume_extraction.apply_flat_failed", error=str(exc))

    valid_skills = [s for s in skills if isinstance(s, dict) and s.get("name")]
    if valid_skills:
        try:
            await replace_all_skills(profile_id, valid_skills, session)
        except Exception as exc:
            await log.awarning("resume_extraction.apply_skills_failed", error=str(exc))

    valid_experiences = []
    for exp in experiences:
        if not isinstance(exp, dict) or not exp.get("company") or not exp.get("title"):
            continue
        exp_copy = dict(exp)
        for date_field in ("start_date", "end_date"):
            val = exp_copy.get(date_field)
            if isinstance(val, str):
                try:
                    parsed = datetime.fromisoformat(val)
                    exp_copy[date_field] = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                except ValueError:
                    exp_copy[date_field] = None
        if not exp_copy.get("start_date"):
            continue
        valid_experiences.append(exp_copy)
    if valid_experiences:
        try:
            await replace_all_work_experiences(profile_id, valid_experiences, session)
        except Exception as exc:
            await log.awarning("resume_extraction.apply_experiences_failed", error=str(exc))


async def get_skills(profile_id: uuid.UUID, session: AsyncSession) -> list[Skill]:
    result = await session.execute(
        select(Skill).where(Skill.profile_id == profile_id)
    )
    return list(result.scalars().all())


async def get_work_experiences(
    profile_id: uuid.UUID, session: AsyncSession
) -> list[WorkExperience]:
    result = await session.execute(
        select(WorkExperience).where(WorkExperience.profile_id == profile_id)
    )
    return list(result.scalars().all())


async def add_skill(profile_id: uuid.UUID, skill_data: dict, session: AsyncSession) -> Skill:
    skill = Skill(profile_id=profile_id, **skill_data)
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return skill


async def remove_skill(skill_id: uuid.UUID, session: AsyncSession) -> None:
    skill = await session.get(Skill, skill_id)
    if skill:
        await session.delete(skill)
        await session.commit()


async def add_work_experience(
    profile_id: uuid.UUID, exp_data: dict, session: AsyncSession
) -> WorkExperience:
    exp = WorkExperience(profile_id=profile_id, **exp_data)
    session.add(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


async def remove_work_experience(exp_id: uuid.UUID, session: AsyncSession) -> None:
    exp = await session.get(WorkExperience, exp_id)
    if exp:
        await session.delete(exp)
        await session.commit()


async def replace_all_skills(
    profile_id: uuid.UUID, skills: list[dict], session: AsyncSession
) -> list[Skill]:
    """Delete all existing skills for the profile and insert the new set."""
    await session.execute(delete(Skill).where(Skill.profile_id == profile_id))
    result = []
    for skill_data in skills:
        skill = Skill(profile_id=profile_id, **skill_data)
        session.add(skill)
        result.append(skill)
    await session.commit()
    return result


async def replace_all_work_experiences(
    profile_id: uuid.UUID, experiences: list[dict], session: AsyncSession
) -> list[WorkExperience]:
    """Delete all existing work experiences for the profile and insert the new set."""
    if len(experiences) > 50:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Maximum 50 work experiences allowed")
    await session.execute(delete(WorkExperience).where(WorkExperience.profile_id == profile_id))
    result = []
    for exp_data in experiences:
        exp = WorkExperience(profile_id=profile_id, **exp_data)
        session.add(exp)
        result.append(exp)
    await session.commit()
    return result


async def upsert_skill(
    profile_id: uuid.UUID, skill_data: dict, session: AsyncSession
) -> Skill:
    """Insert or update a skill matched by (profile_id, name)."""
    result = await session.execute(
        select(Skill).where(
            Skill.profile_id == profile_id,
            Skill.name == skill_data["name"],
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in skill_data.items():
            if hasattr(existing, key) and value is not None:
                setattr(existing, key, value)
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing
    return await add_skill(profile_id, skill_data, session)


async def upsert_work_experience(
    profile_id: uuid.UUID, exp_data: dict, session: AsyncSession
) -> WorkExperience:
    """Insert or update a work experience matched by (profile_id, company, title)."""
    result = await session.execute(
        select(WorkExperience).where(
            WorkExperience.profile_id == profile_id,
            WorkExperience.company == exp_data["company"],
            WorkExperience.title == exp_data["title"],
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in exp_data.items():
            if hasattr(existing, key) and value is not None:
                setattr(existing, key, value)
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing
    return await add_work_experience(profile_id, exp_data, session)
