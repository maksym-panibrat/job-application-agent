import asyncio
from logging.config import fileConfig
from urllib.parse import parse_qs, urlparse, urlunparse

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from alembic import context
from app.config import get_settings

# Import all models to register them with SQLModel.metadata
from app.models import *  # noqa: F401, F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    return str(get_settings().database_url)


def _make_engine():
    """Strip asyncpg-incompatible query params (sslmode, channel_binding) and pass ssl=True."""
    raw = get_url()
    parsed = urlparse(raw)
    params = parse_qs(parsed.query, keep_blank_values=True)
    needs_ssl = params.pop("sslmode", [""])[0] == "require"
    params.pop("channel_binding", None)
    # Rebuild query string without stripped params
    new_query = "&".join(f"{k}={v[0]}" for k, v in params.items())
    clean_url = urlunparse(parsed._replace(query=new_query))
    connect_args = {"ssl": True} if needs_ssl else {}
    return create_async_engine(clean_url, poolclass=pool.NullPool, connect_args=connect_args)


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (used by some CI pipelines)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = _make_engine()
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
