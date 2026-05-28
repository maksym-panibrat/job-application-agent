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
from sqlmodel import select

from app.models.subscription import (
    EngagementEvent,
    EntitlementDecision,
    Subscription,
    SubscriptionAccount,
    SubscriptionPlan,
)
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_daily_maintenance


async def _create_user_and_profile(
    db_session,
    search_active: bool = True,
    expires_delta: timedelta | None = None,
    expires_at: datetime | None = None,
) -> tuple[User, UserProfile]:
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    if expires_at is None and expires_delta is not None:
        expires_at = datetime.now(UTC) + expires_delta

    profile = UserProfile(
        user_id=user.id,
        email=user.email,
        search_active=search_active,
        search_expires_at=expires_at,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(profile)
    return user, profile


async def _seed_subscription(
    db_session,
    user_id: uuid.UUID,
    *,
    status: str = "active",
    period_end: datetime | None = None,
) -> Subscription:
    plan = (
        await db_session.execute(select(SubscriptionPlan).where(SubscriptionPlan.tier == "paid"))
    ).scalar_one_or_none()
    if plan is None:
        plan = SubscriptionPlan(
            tier="paid",
            display_name="Paid",
            followed_company_limit=100,
        )
        db_session.add(plan)
        await db_session.flush()

    account = SubscriptionAccount(
        user_id=user_id,
        provider="test",
        provider_customer_id=f"cus_{uuid.uuid4()}",
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(UTC)
    subscription = Subscription(
        user_id=user_id,
        subscription_account_id=account.id,
        plan_id=plan.id,
        provider="test",
        provider_subscription_id=f"sub_{uuid.uuid4()}",
        status=status,
        current_period_start=now - timedelta(days=1),
        current_period_end=period_end or now + timedelta(days=30),
        canceled_at=now if status == "canceled" else None,
    )
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(subscription)
    return subscription


async def _seed_engagement(
    db_session,
    *,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    occurred_at: datetime,
    event_type: str = "profile_updated",
) -> EngagementEvent:
    event = EngagementEvent(
        user_id=user_id,
        profile_id=profile_id,
        event_type=event_type,
        occurred_at=occurred_at,
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)
    return event


async def _decisions_for_profile(db_session, profile_id: uuid.UUID) -> list[EntitlementDecision]:
    return (
        (
            await db_session.execute(
                select(EntitlementDecision)
                .where(EntitlementDecision.profile_id == profile_id)
                .order_by(EntitlementDecision.decided_at, EntitlementDecision.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_expired_search_paused_by_maintenance(db_session, monkeypatch):
    """
    User whose search_expires_at is in the past should be paused
    by run_daily_maintenance().
    """
    # Profile with expiry 2 hours ago
    _, profile = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(hours=-2)
    )
    assert profile.search_active is True

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False

    decisions = await _decisions_for_profile(db_session, profile.id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.decision_type == "search_paused"
    assert decision.reason == "inactivity"
    assert decision.previous_value == {
        "search_active": True,
        "search_expires_at": profile.search_expires_at.isoformat(),
    }
    assert decision.next_value == {
        "search_active": False,
        "search_expires_at": profile.search_expires_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_paid_active_expired_search_extended_by_maintenance(db_session):
    """Paid active users receive a fresh search expiry instead of being paused."""
    now = datetime.now(UTC)
    user, profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_delta=timedelta(hours=-2),
    )
    await _seed_subscription(
        db_session,
        user.id,
        status="active",
        period_end=now + timedelta(days=30),
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)

    decisions = await _decisions_for_profile(db_session, profile.id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.decision_type == "search_expiry_extended"
    assert decision.reason == "paid_entitlement"
    assert decision.next_value == {
        "search_active": True,
        "search_expires_at": profile.search_expires_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_paid_active_future_expiry_rerun_does_not_duplicate_decision(db_session):
    """Maintenance is idempotent for paid profiles whose expiry is already fresh."""
    now = datetime.now(UTC)
    user, profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_at=now + timedelta(days=7),
    )
    await _seed_subscription(
        db_session,
        user.id,
        status="active",
        period_end=now + timedelta(days=30),
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at <= now + timedelta(days=7, minutes=1)
    assert await _decisions_for_profile(db_session, profile.id) == []


@pytest.mark.asyncio
async def test_canceled_before_period_end_extends_by_maintenance(db_session):
    """Canceled users retain paid entitlement until current_period_end."""
    now = datetime.now(UTC)
    user, profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_delta=timedelta(hours=-2),
    )
    await _seed_subscription(
        db_session,
        user.id,
        status="canceled",
        period_end=now + timedelta(days=5),
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)


@pytest.mark.asyncio
async def test_canceled_after_period_end_free_expired_search_paused(db_session):
    """Canceled users after current_period_end fall back to free inactivity rules."""
    now = datetime.now(UTC)
    user, profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_delta=timedelta(hours=-2),
    )
    await _seed_subscription(
        db_session,
        user.id,
        status="canceled",
        period_end=now - timedelta(seconds=1),
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False


@pytest.mark.asyncio
async def test_future_expiry_not_paused(db_session, monkeypatch):
    """
    User whose search_expires_at is in the future should NOT be paused.
    """
    _, profile = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(days=5)
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert await _decisions_for_profile(db_session, profile.id) == []


@pytest.mark.asyncio
async def test_active_no_expiry_receives_new_expiry(db_session):
    """
    User with search_active=True but no search_expires_at receives a fresh expiry.
    """
    now = datetime.now(UTC)
    _, profile = await _create_user_and_profile(db_session, search_active=True, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)

    decisions = await _decisions_for_profile(db_session, profile.id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.decision_type == "search_expiry_seeded"
    assert decision.reason == "missing_expiry"
    assert decision.previous_value == {"search_active": True, "search_expires_at": None}
    assert decision.next_value == {
        "search_active": True,
        "search_expires_at": profile.search_expires_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_inactive_null_expiry_stays_paused(db_session):
    """Inactive users with no expiry are left paused."""
    _, profile = await _create_user_and_profile(
        db_session, search_active=False, expires_delta=None
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False
    assert profile.search_expires_at is None
    assert await _decisions_for_profile(db_session, profile.id) == []


@pytest.mark.asyncio
async def test_already_inactive_stays_inactive(db_session):
    """
    User with search_active=False is not touched by maintenance.
    """
    _, profile = await _create_user_and_profile(
        db_session, search_active=False, expires_delta=timedelta(hours=-1)
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False


@pytest.mark.asyncio
async def test_free_expired_search_with_recent_engagement_extended(db_session):
    """Recent active engagement extends a free expired search instead of pausing it."""
    now = datetime.now(UTC)
    user, profile = await _create_user_and_profile(
        db_session,
        search_active=True,
        expires_at=now - timedelta(hours=2),
    )
    event = await _seed_engagement(
        db_session,
        user_id=user.id,
        profile_id=profile.id,
        event_type="resume_uploaded",
        occurred_at=now - timedelta(days=2),
    )

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
    assert profile.search_expires_at > now + timedelta(days=6)

    decisions = await _decisions_for_profile(db_session, profile.id)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.decision_type == "search_expiry_extended"
    assert decision.reason == "active_engagement"
    assert decision.source_event_type == event.event_type
    assert decision.source_event_id == event.id


@pytest.mark.asyncio
async def test_multiple_users_only_expired_paused(db_session):
    """Only expired profiles are paused; active ones are left alone."""
    _, expired = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(hours=-1)
    )
    _, active = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(days=3)
    )
    _, no_expiry = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=None
    )

    await run_daily_maintenance()

    await db_session.refresh(expired)
    await db_session.refresh(active)
    await db_session.refresh(no_expiry)

    assert expired.search_active is False
    assert active.search_active is True
    assert no_expiry.search_active is True
    assert no_expiry.search_expires_at is not None
