"""
Integration tests for search auto-pause logic.

Verifies that run_daily_maintenance():
- Pauses users whose search_expires_at is in the past
- Does not pause users whose search_expires_at is in the future
- Skips users with search_active already False
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_daily_maintenance


async def _create_user_and_profile(
    db_session,
    search_active: bool = True,
    expires_delta: timedelta | None = None,
    subscription_plan: str = "free",
    subscription_status: str = "inactive",
) -> UserProfile:
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4()}@test.com",
        subscription_plan=subscription_plan,
        subscription_status=subscription_status,
    )
    db_session.add(user)
    await db_session.commit()

    expires_at = None
    if expires_delta is not None:
        expires_at = datetime.now(UTC) + expires_delta

    profile = UserProfile(
        user_id=user.id,
        search_active=search_active,
        search_expires_at=expires_at,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_expired_search_paused_by_maintenance(db_session, monkeypatch):
    """
    User whose search_expires_at is in the past should be paused
    by run_daily_maintenance().
    """
    # Profile with expiry 2 hours ago
    profile = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(hours=-2)
    )
    assert profile.search_active is True

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False


@pytest.mark.asyncio
async def test_paid_active_expired_search_extended_by_maintenance(db_session):
    """Paid active users receive a fresh search expiry instead of being paused."""
    now = datetime.now(UTC)
    profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_delta=timedelta(hours=-2),
        subscription_plan="paid",
        subscription_status="active",
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)


@pytest.mark.asyncio
async def test_future_expiry_not_paused(db_session, monkeypatch):
    """
    User whose search_expires_at is in the future should NOT be paused.
    """
    profile = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(days=5)
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True


@pytest.mark.asyncio
async def test_active_no_expiry_receives_new_expiry(db_session):
    """
    User with search_active=True but no search_expires_at receives a fresh expiry.
    """
    now = datetime.now(UTC)
    profile = await _create_user_and_profile(db_session, search_active=True, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)


@pytest.mark.asyncio
async def test_inactive_null_expiry_stays_paused(db_session):
    """Inactive users with no expiry are left paused."""
    profile = await _create_user_and_profile(
        db_session, search_active=False, expires_delta=None
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False
    assert profile.search_expires_at is None


@pytest.mark.asyncio
async def test_already_inactive_stays_inactive(db_session):
    """
    User with search_active=False is not touched by maintenance.
    """
    profile = await _create_user_and_profile(
        db_session, search_active=False, expires_delta=timedelta(hours=-1)
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False


@pytest.mark.asyncio
async def test_multiple_users_only_expired_paused(db_session):
    """Only expired profiles are paused; active ones are left alone."""
    expired = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(hours=-1)
    )
    active = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(days=3)
    )
    no_expiry = await _create_user_and_profile(db_session, search_active=True, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(expired)
    await db_session.refresh(active)
    await db_session.refresh(no_expiry)

    assert expired.search_active is False
    assert active.search_active is True
    assert no_expiry.search_active is True
    assert no_expiry.search_expires_at is not None
