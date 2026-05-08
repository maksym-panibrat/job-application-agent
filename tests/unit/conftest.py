"""Unit-test fixtures.

Most unit tests don't need Postgres, but a handful of services have semantics
(e.g. INSERT ... ON CONFLICT) that only Postgres provides. The `db_session`
fixture below mirrors the integration-conftest version: testcontainers
Postgres, schema-per-test, async session.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

import app.models  # noqa: F401 — registers all SQLModel tables with metadata


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def asyncpg_url(postgres_container):
    raw = postgres_container.get_connection_url()
    return raw.replace("psycopg2", "asyncpg").replace(
        "postgresql+asyncpg://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def db_session(asyncpg_url):
    """Per-test async session against a clean schema."""
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()
