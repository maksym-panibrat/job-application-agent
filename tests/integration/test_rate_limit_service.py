"""
Integration tests for the rate limit and usage quota service.

Tests call service functions directly against the testcontainers Postgres DB.
The service commits inside each call, so each invocation is permanent within the test.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services.rate_limit_service import check_daily_quota, check_rate_limit


@pytest.mark.asyncio
async def test_check_rate_limit_passes_under_limit(db_session):
    key = f"rl-test-{uuid.uuid4()}"
    await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    # No exception raised = pass


@pytest.mark.asyncio
async def test_check_rate_limit_raises_at_limit(db_session):
    """After limit+1 calls, the (limit+1)th call raises HTTP 429."""
    key = f"rl-test-{uuid.uuid4()}"
    for _ in range(3):
        await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(key, limit=3, window_seconds=3600, session=db_session)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_check_rate_limit_different_keys_are_independent(db_session):
    """Two different keys do not share counters."""
    key_a = f"rl-a-{uuid.uuid4()}"
    key_b = f"rl-b-{uuid.uuid4()}"
    for _ in range(3):
        await check_rate_limit(key_a, limit=3, window_seconds=3600, session=db_session)
    # key_b should still be at 0 — no exception
    await check_rate_limit(key_b, limit=3, window_seconds=3600, session=db_session)


@pytest.mark.asyncio
async def test_sliding_window_resets_counter(db_session):
    """A new window start means a fresh counter — previous window's exhausted limit doesn't carry over."""  # noqa: E501
    key = f"rl-window-{uuid.uuid4()}"
    limit = 1
    window_seconds = 3600

    t1 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 1, 2, 0, 0, tzinfo=UTC)  # 2 hours later — new window

    with patch(
        "app.services.rate_limit_service._window_start",
        return_value=t1,
    ):
        # Exhaust limit in window 1
        await check_rate_limit(key, limit=limit, window_seconds=window_seconds, session=db_session)
        with pytest.raises(HTTPException):
            await check_rate_limit(
                key, limit=limit, window_seconds=window_seconds, session=db_session
            )

    with patch(
        "app.services.rate_limit_service._window_start",
        return_value=t2,
    ):
        # Window 2 is fresh — should not raise
        await check_rate_limit(key, limit=limit, window_seconds=window_seconds, session=db_session)


@pytest.mark.asyncio
async def test_check_daily_quota_passes_under_limit(db_session):
    user_id = uuid.uuid4()
    await check_daily_quota(user_id, action="resume_upload", limit=3, session=db_session)
    # No exception = pass


@pytest.mark.asyncio
async def test_check_daily_quota_raises_at_limit(db_session):
    """After limit+1 calls with the same user+action+day, raises HTTP 429."""
    user_id = uuid.uuid4()
    for _ in range(3):
        await check_daily_quota(
            user_id, action="resume_upload", limit=3, session=db_session
        )
    with pytest.raises(HTTPException) as exc_info:
        await check_daily_quota(
            user_id, action="resume_upload", limit=3, session=db_session
        )
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_check_daily_quota_different_users_are_independent(db_session):
    """Two different users do not share daily quota counters."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    for _ in range(3):
        await check_daily_quota(
            user_a, action="resume_upload", limit=3, session=db_session
        )
    # user_b should still be at 0
    await check_daily_quota(user_b, action="resume_upload", limit=3, session=db_session)
