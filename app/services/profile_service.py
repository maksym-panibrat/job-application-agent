import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.user_profile import Skill, UserProfile, WorkExperience
from app.sources.resume_parser import parse_resume


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
    profile.updated_at = datetime.utcnow()
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
    profile.updated_at = datetime.utcnow()
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


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
