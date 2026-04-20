from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import get_settings

engine = None
async_session_factory = None


def _build_engine_url(raw_url: str) -> tuple[str, dict]:
    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    needs_ssl = params.pop("sslmode", [""])[0] == "require"
    params.pop("channel_binding", None)
    new_query = "&".join(f"{k}={v[0]}" for k, v in params.items())
    clean_url = urlunparse(parsed._replace(query=new_query))
    connect_args = {"ssl": True} if needs_ssl else {}
    return clean_url, connect_args


def get_engine():
    global engine
    if engine is None:
        settings = get_settings()
        clean_url, connect_args = _build_engine_url(str(settings.database_url))
        engine = create_async_engine(
            clean_url,
            echo=settings.environment == "development",
            pool_size=5,
            max_overflow=2,
            connect_args=connect_args,
        )
    return engine


def get_session_factory():
    global async_session_factory
    if async_session_factory is None:
        async_session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return async_session_factory


async def init_db():
    """Create tables if they don't exist (dev only; prod uses alembic)."""
    async with get_engine().begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
