import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.user import User
from app.services import profile_service


@pytest.mark.asyncio
async def test_get_or_create_profile_recovers_from_concurrent_insert(
    asyncpg_url,
    db_session,
    monkeypatch,
):
    user = User(
        id=uuid.uuid4(),
        email=f"race-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    original_get = profile_service.get_profile_by_user
    stale_reads_remaining = 2

    async def stale_first_read(user_id, session):
        nonlocal stale_reads_remaining
        if stale_reads_remaining > 0:
            stale_reads_remaining -= 1
            return None
        return await original_get(user_id, session)

    monkeypatch.setattr(profile_service, "get_profile_by_user", stale_first_read)

    engine = create_async_engine(asyncpg_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_profile():
        async with factory() as session:
            return await profile_service.get_or_create_profile(user.id, session)

    first = await create_profile()
    second = await create_profile()

    assert first.user_id == user.id
    assert second.user_id == user.id
    assert first.id == second.id

    await engine.dispose()
