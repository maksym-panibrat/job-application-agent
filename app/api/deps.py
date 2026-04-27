import uuid

import jwt
import structlog
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.models.user_profile import UserProfile

log = structlog.get_logger()

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/jwt/login", auto_error=False)


async def get_current_user(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    token: str | None = Depends(_oauth2_scheme),
) -> User:
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
    except jwt.ExpiredSignatureError:
        await log.awarning("auth.token_expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.InvalidSignatureError:
        await log.awarning("auth.token_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.PyJWTError as exc:
        await log.awarning("auth.token_invalid", error_type=type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid token")
    if user_id_str is None:
        await log.awarning("auth.token_missing_sub")
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await session.get(User, uuid.UUID(user_id_str))
    if user is None:
        await log.awarning("auth.user_not_found", user_id=user_id_str)
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if not user.is_active:
        await log.awarning("auth.user_inactive", user_id=user_id_str)
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_current_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserProfile:
    from app.services import profile_service

    return await profile_service.get_or_create_profile(user.id, session)
