import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.subscription import Subscription, SubscriptionAccount, SubscriptionPlan
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services import profile_service
from app.services.entitlements import get_subscription_snapshot


async def _seed_paid_subscription(db_session, user_id: uuid.UUID) -> None:
    paid_plan = SubscriptionPlan(
        tier="paid",
        display_name="Paid",
        followed_company_limit=100,
    )
    db_session.add(paid_plan)
    await db_session.flush()

    account = SubscriptionAccount(
        user_id=user_id,
        provider="test",
        provider_customer_id=f"cus_{uuid.uuid4()}",
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add(
        Subscription(
            user_id=user_id,
            subscription_account_id=account.id,
            plan_id=paid_plan.id,
            provider="test",
            provider_subscription_id=f"sub_{uuid.uuid4()}",
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=30),
        )
    )
    await db_session.commit()


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


@pytest.mark.asyncio
async def test_get_or_create_profile_sets_initial_search_expiry(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"trial-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    before = datetime.now(UTC)
    profile = await profile_service.get_or_create_profile(user.id, db_session)
    after = datetime.now(UTC)

    assert profile.search_active is True
    assert profile.search_expires_at is not None
    settings = get_settings()
    assert before + timedelta(days=settings.search_auto_pause_days) <= profile.search_expires_at
    assert profile.search_expires_at <= after + timedelta(days=settings.search_auto_pause_days)


@pytest.mark.asyncio
async def test_update_profile_loads_free_entitlements_when_target_companies_change(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"free-limit-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    profile = UserProfile(user_id=user.id)
    db_session.add(user)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    with pytest.raises(ValueError, match="Free accounts can follow up to 5 companies"):
        await profile_service.update_profile(
            profile.id,
            {"target_company_ids": [str(uuid.uuid4()) for _ in range(6)]},
            db_session,
        )


@pytest.mark.asyncio
async def test_update_profile_loads_paid_entitlements_when_target_companies_change(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"paid-limit-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    profile = UserProfile(user_id=user.id)
    db_session.add(user)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    await _seed_paid_subscription(db_session, user.id)

    requested_ids = [uuid.uuid4() for _ in range(6)]

    updated = await profile_service.update_profile(
        profile.id,
        {"target_company_ids": [str(company_id) for company_id in requested_ids]},
        db_session,
    )

    assert updated.target_company_ids == requested_ids


@pytest.mark.asyncio
async def test_get_subscription_snapshot_returns_newest_subscription_by_created_at(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"subscription-precedence-{uuid.uuid4()}@test.com",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        hashed_password="",
    )
    paid_plan = SubscriptionPlan(
        tier="paid",
        display_name="Paid",
        followed_company_limit=100,
    )
    alternate_plan = SubscriptionPlan(
        tier="expired-limited",
        display_name="Expired Limited",
        followed_company_limit=3,
    )
    db_session.add_all([user, paid_plan, alternate_plan])
    await db_session.flush()

    account = SubscriptionAccount(
        user_id=user.id,
        provider="test",
        provider_customer_id=f"cus_{uuid.uuid4()}",
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(UTC)
    older_created_at = now - timedelta(days=10)
    newer_created_at = now - timedelta(days=1)
    db_session.add_all(
        [
            Subscription(
                user_id=user.id,
                subscription_account_id=account.id,
                plan_id=paid_plan.id,
                provider="test",
                provider_subscription_id=f"sub_{uuid.uuid4()}",
                status="active",
                current_period_start=now - timedelta(days=20),
                current_period_end=now + timedelta(days=20),
                created_at=older_created_at,
                updated_at=older_created_at,
            ),
            Subscription(
                user_id=user.id,
                subscription_account_id=account.id,
                plan_id=alternate_plan.id,
                provider="test",
                provider_subscription_id=f"sub_{uuid.uuid4()}",
                status="expired",
                current_period_start=now - timedelta(days=8),
                current_period_end=now - timedelta(days=1),
                created_at=newer_created_at,
                updated_at=newer_created_at,
            ),
        ]
    )
    await db_session.commit()

    snapshot = await get_subscription_snapshot(user.id, db_session)

    assert snapshot is not None
    assert snapshot.tier == "expired-limited"
    assert snapshot.status == "expired"
    assert snapshot.current_period_end < now
    assert snapshot.followed_company_limit == 3
