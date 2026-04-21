"""
Integration tests for source cursor advancement.

Verifies that sync_profile() correctly:
- Reads the cursor position from profile.source_cursors
- Passes it to the source adapter
- Writes the next cursor back to the profile
- Advances to the next page on subsequent syncs
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.job_sync_service import sync_profile
from app.sources.base import JobData


def _make_job(external_id: str) -> JobData:
    return JobData(
        external_id=external_id,
        title="Python Engineer",
        company_name="Corp",
        apply_url="https://example.com/apply",
        description_md="Python job.",
    )


def _mock_source(name: str, jobs: list[JobData], next_cursor):
    source = MagicMock()
    source.source_name = name
    source.search = AsyncMock(return_value=(jobs, next_cursor))
    return source


async def _setup_profile(db_session) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"cursor-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        search_keywords=["python"],
        target_locations=["New York"],
        source_cursors={},
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_first_sync_starts_at_page_1(db_session):
    """First sync uses cursor=1 (default) for a new query."""
    profile = await _setup_profile(db_session)
    mock_source = _mock_source("test_src", [_make_job("job-1")], next_cursor=2)

    await sync_profile(profile, db_session, sources=[mock_source])

    # Verify the source was called with cursor=1 (initial)
    call_args = mock_source.search.call_args
    cursor_arg = call_args.args[2]  # positional: query, location, cursor, settings, session
    assert cursor_arg == 1


@pytest.mark.asyncio
async def test_cursor_advances_after_first_sync(db_session):
    """After first sync, source_cursors stores the next page cursor."""
    profile = await _setup_profile(db_session)
    mock_source = _mock_source("test_src", [_make_job("job-1")], next_cursor=2)

    await sync_profile(profile, db_session, sources=[mock_source])

    await db_session.refresh(profile)
    cursors = profile.source_cursors
    assert "test_src" in cursors
    query_key = "python|New York"
    assert cursors["test_src"][query_key] == 2


@pytest.mark.asyncio
async def test_second_sync_uses_advanced_cursor(db_session):
    """Second sync passes cursor=2, gets page-2 results."""
    profile = await _setup_profile(db_session)

    # First sync: cursor 1 → 2
    source_call_1 = _mock_source("test_src", [_make_job("job-1")], next_cursor=2)
    await sync_profile(profile, db_session, sources=[source_call_1])

    await db_session.refresh(profile)

    # Second sync: cursor should be 2
    source_call_2 = _mock_source("test_src", [_make_job("job-2")], next_cursor=3)
    await sync_profile(profile, db_session, sources=[source_call_2])

    call_args = source_call_2.search.call_args
    cursor_arg = call_args.args[2]
    assert cursor_arg == 2


@pytest.mark.asyncio
async def test_different_sources_have_independent_cursors(db_session):
    """Two different sources maintain independent cursor state."""
    profile = await _setup_profile(db_session)

    source_a = _mock_source("source_a", [_make_job("a-1")], next_cursor=2)
    source_b = _mock_source("source_b", [_make_job("b-1")], next_cursor=5)

    await sync_profile(profile, db_session, sources=[source_a, source_b])

    await db_session.refresh(profile)
    cursors = profile.source_cursors
    query_key = "python|New York"

    assert cursors["source_a"][query_key] == 2
    assert cursors["source_b"][query_key] == 5


@pytest.mark.asyncio
async def test_sync_is_idempotent_for_jobs(db_session):
    """Running sync twice with the same job doesn't create duplicates."""
    from sqlmodel import select

    from app.models.job import Job

    profile = await _setup_profile(db_session)
    job = _make_job("unique-job-001")

    source_1 = _mock_source("test_src", [job], next_cursor=2)
    source_2 = _mock_source("test_src", [job], next_cursor=3)

    await sync_profile(profile, db_session, sources=[source_1])
    # Manually reset cursor to re-fetch same job (simulates replay)
    profile.source_cursors = {}
    db_session.add(profile)
    await db_session.commit()

    await sync_profile(profile, db_session, sources=[source_2])

    result = await db_session.execute(select(Job).where(Job.external_id == "unique-job-001"))
    jobs = result.scalars().all()
    assert len(jobs) == 1  # no duplicate
