import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.models.user_profile import UserProfile

SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if not settings.auth_enabled:
        user = await session.get(User, SINGLE_USER_ID)
        if user is None:
            user = User(
                id=SINGLE_USER_ID,
                email="dev@local",
                is_active=True,
                is_verified=True,
                is_superuser=True,
                hashed_password="",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user
    raise HTTPException(status_code=501, detail="JWT auth not yet implemented")


async def get_current_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserProfile:
    from app.services import profile_service

    return await profile_service.get_or_create_profile(user.id, session)
