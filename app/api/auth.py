"""
fastapi-users Google OAuth wiring.
Only mounted when settings.auth_enabled = True.
"""

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.oauth_account import OAuthAccount
from app.models.user import User


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def on_after_register(self, user: User, request=None):
        pass

    async def on_after_login(self, user: User, request=None, response=None):
        pass


async def get_user_db(session: AsyncSession = Depends(get_db)) -> AsyncGenerator:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(user_db=Depends(get_user_db)) -> AsyncGenerator:
    settings = get_settings()
    manager = UserManager(user_db)
    manager.reset_password_token_secret = settings.jwt_secret.get_secret_value()
    manager.verification_token_secret = settings.jwt_secret.get_secret_value()
    yield manager


def get_jwt_strategy() -> JWTStrategy:
    settings = get_settings()
    return JWTStrategy(
        secret=settings.jwt_secret.get_secret_value(),
        lifetime_seconds=86400,  # 24 h
    )


bearer_transport = BearerTransport(tokenUrl="/auth/jwt/login")

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


def get_google_oauth_client() -> GoogleOAuth2:
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set"
            " when AUTH_ENABLED=true"
        )
    return GoogleOAuth2(
        settings.google_oauth_client_id.get_secret_value(),
        settings.google_oauth_client_secret.get_secret_value(),
    )


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
current_active_user = fastapi_users.current_user(active=True)
