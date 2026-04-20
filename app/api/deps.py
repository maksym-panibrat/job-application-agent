import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.models.user_profile import UserProfile

SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/jwt/login", auto_error=False)


async def get_current_user(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Depends(_oauth2_scheme),
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

    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=["fastapi-users:auth"],
        )
        user_id_str = data.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if user_id_str is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await session.get(User, uuid.UUID(user_id_str))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_current_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserProfile:
    from app.services import profile_service

    return await profile_service.get_or_create_profile(user.id, session)
