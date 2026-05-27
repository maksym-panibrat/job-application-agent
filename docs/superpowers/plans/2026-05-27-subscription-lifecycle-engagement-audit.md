# Subscription Lifecycle and Engagement Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the current subscription-entitlements PR so subscription lifecycle, engagement evidence, and entitlement decisions are canonical, auditable, and ready for an imminent paid-service integration.

**Architecture:** Move subscription state out of `users` into dedicated lifecycle tables. Keep product analytics `events` separate from server-authored `engagement_events`, and use `entitlement_decisions` for derived policy decisions. The frontend consumes a profile read model with `subscription`, `entitlements`, and `limits`; backend services own all entitlement logic.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy/Alembic, PostgreSQL JSONB, pytest/testcontainers, React, TypeScript, TanStack Query, Vitest, Playwright.

---

## File Structure

- Modify `app/models/user.py`: remove subscription lifecycle fields from `User`.
- Create `app/models/subscription.py`: `SubscriptionPlan`, `SubscriptionAccount`, `Subscription`, `SubscriptionEvent`, `EngagementEvent`, `EntitlementDecision`, status/type constants.
- Modify `app/models/__init__.py`: import/export new models for Alembic metadata.
- Replace `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py`: create canonical tables, seed `subscription_plans`, backfill active profile expiry; do not alter `users`.
- Replace `app/services/entitlements.py`: plan/subscription read model, effective entitlement calculation, follow-limit validation, search expiry helpers, decision-writing helpers.
- Create `app/services/engagement_service.py`: server-authored active engagement recording.
- Modify `app/services/profile_service.py`: use entitlement read model for company limits; record engagement for profile/company changes where the API owns the action.
- Modify `app/api/profile.py`: expose new frontend profile API shape; record resume/search engagement; pass entitlement context to profile updates.
- Modify `app/agents/onboarding.py`: validate company additions through entitlement service and record `company_followed` engagement.
- Modify `app/api/applications.py`: record `application_dismissed` and `application_applied` engagement after successful state transitions.
- Modify `app/scheduler/tasks.py`: daily maintenance evaluates subscription lifecycle, engagement recency, search expiry/pause, and writes entitlement decisions.
- Modify `frontend/src/api/client.ts`: update `Profile` subscription/entitlement types.
- Modify `frontend/src/pages/Settings.tsx`: pass `searchAutoPause` from `profile.entitlements.search_auto_pause`.
- Modify `frontend/src/components/settings/SearchToggleSection.tsx`: replace `paidActive` prop with `searchAutoPause`.
- Update frontend tests and Playwright screenshot fixtures for the new profile shape.
- Update backend unit/integration tests around entitlement calculation, migration shape, profile API, onboarding, applications, engagement, and maintenance.

---

## Task 1: Canonical Subscription Models and Migration

**Files:**
- Modify: `app/models/user.py`
- Create: `app/models/subscription.py`
- Modify: `app/models/__init__.py`
- Replace: `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py`
- Test: `tests/integration/test_subscription_entitlements_migration.py`

- [ ] **Step 1: Replace migration tests with canonical schema expectations**

Edit `tests/integration/test_subscription_entitlements_migration.py` so it no longer expects columns on `users`. The core tests should include this shape:

```python
@pytest.mark.asyncio
async def test_user_subscription_columns_are_not_present(db_session):
    result = await db_session.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users'
              AND column_name IN (
                'subscription_plan',
                'subscription_status',
                'subscription_current_period_end'
              )
        """)
    )

    assert result.fetchall() == []
```

Add table/seed checks:

```python
@pytest.mark.asyncio
async def test_subscription_tables_and_seed_plans_exist(db_session):
    tables = (
        await db_session.execute(
            text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'subscription_plans',
                    'subscription_accounts',
                    'subscriptions',
                    'subscription_events',
                    'engagement_events',
                    'entitlement_decisions'
                  )
            """)
        )
    ).scalars().all()

    assert set(tables) == {
        "subscription_plans",
        "subscription_accounts",
        "subscriptions",
        "subscription_events",
        "engagement_events",
        "entitlement_decisions",
    }

    rows = (
        await db_session.execute(
            text("""
                SELECT tier, followed_company_limit, valid_until
                FROM subscription_plans
                ORDER BY tier
            """)
        )
    ).mappings().all()

    assert rows == [
        {"tier": "free", "followed_company_limit": 5, "valid_until": None},
        {"tier": "paid", "followed_company_limit": 100, "valid_until": None},
    ]
```

Keep the existing backfill test, but rename it from subscription-entitlement backfill to search-expiry backfill. It should still assert active profiles with `search_expires_at IS NULL` get a value and inactive/future-expiry profiles are not overwritten.

- [ ] **Step 2: Run migration tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_subscription_entitlements_migration.py -v
```

Expected: FAIL because subscription tables/models do not exist and `users.subscription_*` still exists in this branch.

- [ ] **Step 3: Remove subscription fields from `User`**

In `app/models/user.py`, remove the `datetime` and `sqlalchemy as sa` imports if no longer used, and remove these fields from `User`:

```python
subscription_plan: str = Field(...)
subscription_status: str = Field(...)
subscription_current_period_end: datetime | None = Field(...)
```

Keep the rest of the model unchanged.

- [ ] **Step 4: Add subscription models**

Create `app/models/subscription.py`:

```python
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


SUBSCRIPTION_STATUSES = ("active", "canceled", "expired", "refunded", "chargeback", "revoked")
SUBSCRIPTION_EVENT_TYPES = (
    "subscription_created",
    "subscription_renewed",
    "subscription_canceled",
    "subscription_expired",
    "subscription_refunded",
    "subscription_chargeback",
    "subscription_revoked",
    "subscription_reactivated",
    "subscription_plan_changed",
)
ENGAGEMENT_EVENT_TYPES = (
    "company_followed",
    "company_unfollowed",
    "profile_updated",
    "resume_uploaded",
    "application_dismissed",
    "application_applied",
    "chat_message_sent",
    "search_resumed",
)
ENTITLEMENT_DECISION_TYPES = (
    "follow_limit_applied",
    "follow_limit_rejected",
    "subscription_plan_rejected",
    "search_expiry_seeded",
    "search_expiry_extended",
    "search_paused",
    "paid_entitlement_activated",
    "paid_entitlement_ended",
    "over_limit_companies_preserved",
)


class SubscriptionPlan(SQLModel, table=True):
    __tablename__ = "subscription_plans"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tier: str = Field(index=True, unique=True, max_length=64)
    display_name: str = Field(max_length=128)
    followed_company_limit: int = Field(nullable=False)
    valid_from: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    valid_until: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SubscriptionAccount(SQLModel, table=True):
    __tablename__ = "subscription_accounts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    provider: str = Field(index=True, max_length=64)
    provider_customer_id: str = Field(max_length=256)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "provider_customer_id",
            name="uq_subscription_accounts_provider_customer",
        ),
    )


class Subscription(SQLModel, table=True):
    __tablename__ = "subscriptions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    subscription_account_id: uuid.UUID = Field(foreign_key="subscription_accounts.id", index=True)
    plan_id: uuid.UUID = Field(foreign_key="subscription_plans.id", index=True)
    provider: str = Field(index=True, max_length=64)
    provider_subscription_id: str = Field(max_length=256)
    status: str = Field(index=True, max_length=32)
    current_period_start: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    current_period_end: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    canceled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_subscription_id", name="uq_subscriptions_provider_subscription"),
        sa.CheckConstraint(
            "status IN ('active', 'canceled', 'expired', 'refunded', 'chargeback', 'revoked')",
            name="ck_subscriptions_status",
        ),
    )


class SubscriptionEvent(SQLModel, table=True):
    __tablename__ = "subscription_events"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    subscription_id: uuid.UUID = Field(foreign_key="subscriptions.id", index=True)
    event_type: str = Field(index=True, max_length=64)
    provider: str = Field(index=True, max_length=64)
    provider_event_id: str = Field(max_length=256)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))

    __table_args__ = (
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_subscription_events_provider_event"),
        sa.CheckConstraint(
            "event_type IN ('subscription_created', 'subscription_renewed', 'subscription_canceled', 'subscription_expired', 'subscription_refunded', 'subscription_chargeback', 'subscription_revoked', 'subscription_reactivated', 'subscription_plan_changed')",
            name="ck_subscription_events_type",
        ),
    )


class EngagementEvent(SQLModel, table=True):
    __tablename__ = "engagement_events"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id", index=True)
    event_type: str = Field(index=True, max_length=64)
    subject_type: str | None = Field(default=None, max_length=64)
    subject_id: uuid.UUID | None = Field(default=None, index=True)
    source: str = Field(default="api", index=True, max_length=32)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    metadata: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))

    __table_args__ = (
        sa.CheckConstraint(
            "event_type IN ('company_followed', 'company_unfollowed', 'profile_updated', 'resume_uploaded', 'application_dismissed', 'application_applied', 'chat_message_sent', 'search_resumed')",
            name="ck_engagement_events_type",
        ),
    )


class EntitlementDecision(SQLModel, table=True):
    __tablename__ = "entitlement_decisions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id", index=True)
    decision_type: str = Field(index=True, max_length=64)
    previous_value: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    next_value: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    reason: str = Field(index=True, max_length=128)
    source_event_type: str | None = Field(default=None, max_length=64)
    source_event_id: uuid.UUID | None = Field(default=None, index=True)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_column=Column(DateTime(timezone=True), nullable=False, index=True))

    __table_args__ = (
        sa.CheckConstraint(
            "decision_type IN ('follow_limit_applied', 'follow_limit_rejected', 'subscription_plan_rejected', 'search_expiry_seeded', 'search_expiry_extended', 'search_paused', 'paid_entitlement_activated', 'paid_entitlement_ended', 'over_limit_companies_preserved')",
            name="ck_entitlement_decisions_type",
        ),
    )
```

- [ ] **Step 5: Register models**

In `app/models/__init__.py`, import and export:

```python
from app.models.subscription import (  # noqa: F401
    EngagementEvent,
    EntitlementDecision,
    Subscription,
    SubscriptionAccount,
    SubscriptionEvent,
    SubscriptionPlan,
)
```

Add these names to `__all__`.

- [ ] **Step 6: Replace the entitlement migration**

Replace `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py` so `upgrade()` creates the six subscription/audit tables, seeds `free` and `paid`, and runs the existing profile expiry backfill. Use this seed SQL exactly:

```python
op.execute(
    """
    INSERT INTO subscription_plans
        (id, tier, display_name, followed_company_limit, valid_from, valid_until, created_at, updated_at)
    VALUES
        (gen_random_uuid(), 'free', 'Free', 5, NOW(), NULL, NOW(), NOW()),
        (gen_random_uuid(), 'paid', 'Paid', 100, NOW(), NULL, NOW(), NOW())
    ON CONFLICT (tier) DO NOTHING
    """
)
```

The migration must not add or drop columns on `users` because this PR has not merged yet.

- [ ] **Step 7: Run migration tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_subscription_entitlements_migration.py -v
UV_CACHE_DIR=/private/tmp/uv-cache uv run alembic heads
```

Expected: tests PASS; `alembic heads` prints exactly `e1f2a3b4c5d6 (head)`.

- [ ] **Step 8: Commit**

```bash
git add app/models/user.py app/models/subscription.py app/models/__init__.py alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py tests/integration/test_subscription_entitlements_migration.py
git commit -m "feat: add subscription lifecycle schema"
```

---

## Task 2: Entitlement Service Read Model

**Files:**
- Modify: `app/services/entitlements.py`
- Test: `tests/unit/test_entitlements.py`

- [ ] **Step 1: Replace entitlement tests**

Rewrite `tests/unit/test_entitlements.py` around subscription state rather than `User.subscription_*`. Include these tests:

```python
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.services.entitlements import (
    CompanyFollowLimitError,
    EffectiveEntitlements,
    FREE_COMPANY_LIMIT,
    PAID_COMPANY_LIMIT,
    SubscriptionSnapshot,
    company_follow_limit,
    dedupe_company_ids,
    effective_entitlements,
    next_search_expiry,
    validate_company_follow_change,
)


def _sub(status: str, period_end: datetime, tier: str = "paid", limit: int = PAID_COMPANY_LIMIT):
    return SubscriptionSnapshot(
        tier=tier,
        status=status,
        current_period_end=period_end,
        followed_company_limit=limit,
    )


def test_active_before_period_end_grants_paid_access():
    now = datetime(2026, 5, 27, tzinfo=UTC)

    entitlements = effective_entitlements(_sub("active", now + timedelta(days=1)), now)

    assert entitlements == EffectiveEntitlements(
        tier="paid",
        subscription_status="active",
        paid_access=True,
        search_auto_pause=False,
        followed_company_limit=100,
    )


def test_canceled_before_period_end_grants_paid_access():
    now = datetime(2026, 5, 27, tzinfo=UTC)

    entitlements = effective_entitlements(_sub("canceled", now + timedelta(days=1)), now)

    assert entitlements.paid_access is True
    assert entitlements.followed_company_limit == 100


@pytest.mark.parametrize("status", ["expired", "refunded", "chargeback", "revoked"])
def test_terminal_statuses_grant_free_access(status):
    now = datetime(2026, 5, 27, tzinfo=UTC)

    entitlements = effective_entitlements(_sub(status, now + timedelta(days=1)), now)

    assert entitlements.paid_access is False
    assert entitlements.search_auto_pause is True
    assert entitlements.followed_company_limit == 5


def test_past_period_end_grants_free_access_even_if_status_active():
    now = datetime(2026, 5, 27, tzinfo=UTC)

    entitlements = effective_entitlements(_sub("active", now - timedelta(seconds=1)), now)

    assert entitlements.paid_access is False
    assert entitlements.followed_company_limit == 5


def test_no_subscription_grants_free_access():
    now = datetime(2026, 5, 27, tzinfo=UTC)

    entitlements = effective_entitlements(None, now)

    assert entitlements.tier == "free"
    assert entitlements.subscription_status is None
    assert entitlements.paid_access is False
    assert entitlements.search_auto_pause is True
    assert entitlements.followed_company_limit == 5
```

Keep the existing dedupe and over-limit removal/swap tests, but pass an `EffectiveEntitlements` object into `validate_company_follow_change()` instead of a user-like object.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/unit/test_entitlements.py -v
```

Expected: FAIL because `SubscriptionSnapshot`, `EffectiveEntitlements`, and new signatures do not exist.

- [ ] **Step 3: Implement entitlement read model**

Replace `app/services/entitlements.py` with the new dataclass-driven service:

```python
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

FREE_TIER = "free"
FREE_COMPANY_LIMIT = 5
PAID_COMPANY_LIMIT = 100
PAID_ENTITLEMENT_STATUSES = {"active", "canceled"}


@dataclass(frozen=True)
class SubscriptionSnapshot:
    tier: str
    status: str
    current_period_end: datetime
    followed_company_limit: int


@dataclass(frozen=True)
class EffectiveEntitlements:
    tier: str
    subscription_status: str | None
    paid_access: bool
    search_auto_pause: bool
    followed_company_limit: int


class SearchSettings:
    search_auto_pause_days: int


class CompanyFollowLimitError(ValueError):
    def __init__(self, limit: int) -> None:
        account_type = "Paid" if limit > FREE_COMPANY_LIMIT else "Free"
        super().__init__(f"{account_type} accounts can follow up to {limit} companies.")


def effective_entitlements(
    subscription: SubscriptionSnapshot | None,
    now: datetime | None = None,
) -> EffectiveEntitlements:
    now = now or datetime.now(UTC)
    if (
        subscription is not None
        and subscription.status in PAID_ENTITLEMENT_STATUSES
        and subscription.current_period_end > now
    ):
        return EffectiveEntitlements(
            tier=subscription.tier,
            subscription_status=subscription.status,
            paid_access=True,
            search_auto_pause=False,
            followed_company_limit=subscription.followed_company_limit,
        )
    return EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status=subscription.status if subscription is not None else None,
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=FREE_COMPANY_LIMIT,
    )


def company_follow_limit(entitlements: EffectiveEntitlements) -> int:
    return entitlements.followed_company_limit


def next_search_expiry(now: datetime, settings: SearchSettings) -> datetime:
    return now + timedelta(days=settings.search_auto_pause_days)


def dedupe_company_ids(company_ids: Iterable[uuid.UUID | str]) -> list[uuid.UUID]:
    deduped: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for company_id in company_ids:
        normalized_id = company_id if isinstance(company_id, uuid.UUID) else uuid.UUID(company_id)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        deduped.append(normalized_id)
    return deduped


def validate_company_follow_change(
    entitlements: EffectiveEntitlements,
    current_ids: Iterable[uuid.UUID | str],
    requested_ids: Iterable[uuid.UUID | str],
) -> list[uuid.UUID]:
    limit = entitlements.followed_company_limit
    current_deduped_ids = dedupe_company_ids(current_ids)
    requested_deduped_ids = dedupe_company_ids(requested_ids)
    if len(requested_deduped_ids) <= limit:
        return requested_deduped_ids
    current_id_set = set(current_deduped_ids)
    requested_id_set = set(requested_deduped_ids)
    if len(current_deduped_ids) > limit and requested_id_set < current_id_set:
        return requested_deduped_ids
    raise CompanyFollowLimitError(limit)
```

- [ ] **Step 4: Run entitlement tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/unit/test_entitlements.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/entitlements.py tests/unit/test_entitlements.py
git commit -m "feat: add effective entitlement policy"
```

---

## Task 3: Subscription Repository and Profile API Shape

**Files:**
- Modify: `app/services/entitlements.py`
- Modify: `app/api/profile.py`
- Modify: `app/services/profile_service.py`
- Test: `tests/integration/test_company_resolution_flow.py`
- Test: `tests/integration/test_profile_service.py`

- [ ] **Step 1: Add integration tests for new profile read model**

In `tests/integration/test_company_resolution_flow.py`, update the current subscription profile test into two tests:

```python
@pytest.mark.asyncio
async def test_get_profile_includes_free_entitlements_without_subscription(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"] is None
    assert body["entitlements"] == {
        "paid_access": False,
        "search_auto_pause": True,
    }
    assert body["limits"]["followed_companies"] == 5
```

Add a paid subscription fixture inline:

```python
@pytest.mark.asyncio
async def test_get_profile_includes_paid_subscription_and_entitlements(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app
    from app.models.subscription import Subscription, SubscriptionAccount, SubscriptionPlan

    user, _ = seeded_user
    plan = (
        await db_session.execute(select(SubscriptionPlan).where(SubscriptionPlan.tier == "paid"))
    ).scalar_one()
    account = SubscriptionAccount(
        user_id=user.id,
        provider="stripe",
        provider_customer_id="cus_test_profile",
    )
    db_session.add(account)
    await db_session.flush()
    period_end = datetime.now(UTC) + timedelta(days=30)
    db_session.add(
        Subscription(
            user_id=user.id,
            subscription_account_id=account.id,
            plan_id=plan.id,
            provider="stripe",
            provider_subscription_id="sub_test_profile",
            status="active",
            current_period_start=datetime.now(UTC),
            current_period_end=period_end,
        )
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"]["tier"] == "paid"
    assert body["subscription"]["status"] == "active"
    assert body["subscription"]["current_period_end"] is not None
    assert body["entitlements"] == {
        "paid_access": True,
        "search_auto_pause": False,
    }
    assert body["limits"]["followed_companies"] == 100
```

- [ ] **Step 2: Run profile tests to verify failure**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_company_resolution_flow.py::test_get_profile_includes_free_entitlements_without_subscription tests/integration/test_company_resolution_flow.py::test_get_profile_includes_paid_subscription_and_entitlements -v
```

Expected: FAIL because `/api/profile` still returns `subscription.plan/status/paid_active` from `users`.

- [ ] **Step 3: Add async subscription lookup helper**

Extend `app/services/entitlements.py` with:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.subscription import Subscription, SubscriptionPlan


async def get_subscription_snapshot(
    user_id: uuid.UUID,
    session: AsyncSession,
) -> SubscriptionSnapshot | None:
    result = await session.execute(
        select(Subscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == Subscription.plan_id)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    subscription, plan = row
    return SubscriptionSnapshot(
        tier=plan.tier,
        status=subscription.status,
        current_period_end=subscription.current_period_end,
        followed_company_limit=plan.followed_company_limit,
    )
```

- [ ] **Step 4: Update `/api/profile` response**

In `app/api/profile.py`, remove imports of `is_paid_active` and `company_follow_limit(user)`. Import:

```python
from app.services.entitlements import effective_entitlements, get_subscription_snapshot
```

Inside `get_profile()`, before the return:

```python
subscription = await get_subscription_snapshot(user.id, session)
entitlements = effective_entitlements(subscription)
subscription_body = (
    {
        "tier": subscription.tier,
        "status": subscription.status,
        "current_period_end": subscription.current_period_end,
    }
    if subscription is not None
    else None
)
```

Return:

```python
"subscription": subscription_body,
"entitlements": {
    "paid_access": entitlements.paid_access,
    "search_auto_pause": entitlements.search_auto_pause,
},
"limits": {
    "followed_companies": entitlements.followed_company_limit,
},
```

- [ ] **Step 5: Update profile company-limit enforcement**

Change `profile_service.update_profile()` signature from `user: User | None = None` to:

```python
entitlements: EffectiveEntitlements | None = None
```

When `target_company_ids` is present and `entitlements is None`, load it:

```python
subscription = await get_subscription_snapshot(profile.user_id, session)
entitlements = effective_entitlements(subscription)
```

Then call:

```python
data["target_company_ids"] = validate_company_follow_change(
    entitlements,
    profile.target_company_ids or [],
    raw,
)
```

Update `app/api/profile.py` PATCH handler to compute entitlements once and pass `entitlements=entitlements`.

- [ ] **Step 6: Run profile/API tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_company_resolution_flow.py tests/integration/test_profile_service.py -v
```

Expected: PASS after updating any assertions from old `paid_active` shape.

- [ ] **Step 7: Commit**

```bash
git add app/services/entitlements.py app/api/profile.py app/services/profile_service.py tests/integration/test_company_resolution_flow.py tests/integration/test_profile_service.py
git commit -m "feat: expose canonical subscription entitlements"
```

---

## Task 4: Engagement Events and State-Changing Endpoints

**Files:**
- Create: `app/services/engagement_service.py`
- Modify: `app/api/profile.py`
- Modify: `app/services/profile_service.py`
- Modify: `app/agents/onboarding.py`
- Modify: `app/api/applications.py`
- Test: `tests/integration/test_engagement_events.py`
- Test: `tests/integration/test_onboarding_agent.py`
- Test: `tests/integration/test_apply_lifecycle.py`

- [ ] **Step 1: Add engagement integration tests**

Create `tests/integration/test_engagement_events.py`:

```python
from sqlalchemy import text


async def _event_rows(db_session):
    rows = (
        await db_session.execute(
            text("""
                SELECT event_type, subject_type, source
                FROM engagement_events
                ORDER BY occurred_at, id
            """)
        )
    ).mappings().all()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_profile_patch_records_profile_and_company_engagement(
    db_session, auth_headers, seeded_user
):
    from httpx import ASGITransport, AsyncClient
    from app.main import app as fastapi_app
    from tests.integration.test_company_resolution_flow import _seed_companies

    companies = await _seed_companies(db_session, 1)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={
                "target_roles": ["Staff Engineer"],
                "target_company_ids": [str(companies[0].id)],
            },
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert await _event_rows(db_session) == [
        {"event_type": "profile_updated", "subject_type": "profile", "source": "api"},
        {"event_type": "company_followed", "subject_type": "company", "source": "api"},
    ]
```

Add tests for resume upload, search resume, application dismissed, and application applied. Each should assert one matching row in `engagement_events`; do not assert client analytics `events`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_engagement_events.py -v
```

Expected: FAIL because no engagement service writes rows.

- [ ] **Step 3: Implement engagement service**

Create `app/services/engagement_service.py`:

```python
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import EngagementEvent


async def record_engagement(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    event_type: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    source: str = "api",
    metadata: dict[str, Any] | None = None,
) -> EngagementEvent:
    event = EngagementEvent(
        user_id=user_id,
        profile_id=profile_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source,
        metadata=metadata or {},
    )
    session.add(event)
    await session.flush()
    return event
```

- [ ] **Step 4: Record profile and company engagement**

In `app/api/profile.py` PATCH handler, calculate company id deltas before `profile_service.update_profile()`:

```python
before_company_ids = set(profile.target_company_ids or [])
```

After update succeeds, record:

```python
from app.services.engagement_service import record_engagement

if any(k != "target_company_ids" for k in filtered):
    await record_engagement(
        session,
        user_id=profile.user_id,
        profile_id=profile.id,
        event_type="profile_updated",
        subject_type="profile",
        subject_id=profile.id,
    )
after_company_ids = set(updated.target_company_ids or [])
for company_id in sorted(after_company_ids - before_company_ids, key=str):
    await record_engagement(
        session,
        user_id=profile.user_id,
        profile_id=profile.id,
        event_type="company_followed",
        subject_type="company",
        subject_id=company_id,
    )
for company_id in sorted(before_company_ids - after_company_ids, key=str):
    await record_engagement(
        session,
        user_id=profile.user_id,
        profile_id=profile.id,
        event_type="company_unfollowed",
        subject_type="company",
        subject_id=company_id,
    )
await session.commit()
```

Because `profile_service.update_profile()` currently commits, either move engagement recording into `profile_service.update_profile()` before its commit or refactor it to support `commit=False`. Prefer recording inside `profile_service.update_profile()` to keep profile mutation and engagement in one transaction.

- [ ] **Step 5: Record resume and search resume engagement**

In `upload_resume()`, after a successful save:

```python
await record_engagement(
    session,
    user_id=profile.user_id,
    profile_id=profile.id,
    event_type="resume_uploaded",
    subject_type="profile",
    subject_id=profile.id,
    metadata={"extraction_status": extraction_status},
)
await session.commit()
```

In `toggle_search()`, when `search_active` is true:

```python
await record_engagement(
    session,
    user_id=profile.user_id,
    profile_id=profile.id,
    event_type="search_resumed",
    subject_type="profile",
    subject_id=profile.id,
)
await session.commit()
```

- [ ] **Step 6: Record application engagement**

In `app/api/applications.py`, after a successful status change to dismissed:

```python
await record_engagement(
    session,
    user_id=profile.user_id,
    profile_id=profile.id,
    event_type="application_dismissed",
    subject_type="application",
    subject_id=app.id,
)
```

After successful apply in either `review_application()` or `mark_applied()`:

```python
await record_engagement(
    session,
    user_id=profile.user_id,
    profile_id=profile.id,
    event_type="application_applied",
    subject_type="application",
    subject_id=app.id,
)
```

Do not record engagement when `mark_applied()` returns early for an already-applied application.

- [ ] **Step 7: Update onboarding company persistence**

In `app/agents/onboarding.py`, load effective entitlements with `get_subscription_snapshot()` and `effective_entitlements()` instead of loading `User` for old fields. After saving new company ids, record `company_followed` events with `source="agent"` for each newly-added company id.

- [ ] **Step 8: Run engagement tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_engagement_events.py tests/integration/test_onboarding_agent.py tests/integration/test_apply_lifecycle.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/services/engagement_service.py app/api/profile.py app/services/profile_service.py app/agents/onboarding.py app/api/applications.py tests/integration/test_engagement_events.py tests/integration/test_onboarding_agent.py tests/integration/test_apply_lifecycle.py
git commit -m "feat: record active engagement events"
```

---

## Task 5: Maintenance-Owned Search Decisions and Audit

**Files:**
- Modify: `app/services/entitlements.py`
- Modify: `app/scheduler/tasks.py`
- Test: `tests/integration/test_scheduler_expiry.py`

- [ ] **Step 1: Add maintenance tests**

Update `tests/integration/test_scheduler_expiry.py` to seed subscription tables instead of `User.subscription_*`. Add tests:

```python
@pytest.mark.asyncio
async def test_free_expired_search_with_recent_engagement_is_extended(db_session):
    user, profile = await _make_user_profile(
        db_session,
        expires_delta=timedelta(days=-1),
    )
    db_session.add(
        EngagementEvent(
            user_id=user.id,
            profile_id=profile.id,
            event_type="profile_updated",
            subject_type="profile",
            subject_id=profile.id,
            occurred_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    await run_daily_maintenance()
    await db_session.refresh(profile)

    assert profile.search_active is True
    assert profile.search_expires_at > datetime.now(UTC) + timedelta(days=6)
```

Add:

```python
@pytest.mark.asyncio
async def test_search_pause_writes_entitlement_decision(db_session):
    user, profile = await _make_user_profile(
        db_session,
        expires_delta=timedelta(days=-1),
    )

    await run_daily_maintenance()

    row = (
        await db_session.execute(
            select(EntitlementDecision).where(
                EntitlementDecision.profile_id == profile.id,
                EntitlementDecision.decision_type == "search_paused",
            )
        )
    ).scalar_one()
    assert row.reason == "inactivity"
```

Add paid/canceled period-end tests:

```python
@pytest.mark.asyncio
async def test_canceled_before_period_end_extends_search(db_session):
    user, profile = await _make_user_profile(
        db_session,
        expires_delta=timedelta(days=-1),
    )
    await _seed_subscription(db_session, user.id, status="canceled", period_end=datetime.now(UTC) + timedelta(days=3))

    await run_daily_maintenance()
    await db_session.refresh(profile)

    assert profile.search_active is True
    assert profile.search_expires_at > datetime.now(UTC) + timedelta(days=6)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_scheduler_expiry.py -v
```

Expected: FAIL because maintenance still joins `User` and uses old `is_paid_active`.

- [ ] **Step 3: Add decision-writing helper**

In `app/services/entitlements.py`, add:

```python
from app.models.subscription import EntitlementDecision


async def record_entitlement_decision(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    decision_type: str,
    reason: str,
    previous_value: dict | None = None,
    next_value: dict | None = None,
    source_event_type: str | None = None,
    source_event_id: uuid.UUID | None = None,
) -> EntitlementDecision:
    decision = EntitlementDecision(
        user_id=user_id,
        profile_id=profile_id,
        decision_type=decision_type,
        previous_value=previous_value,
        next_value=next_value,
        reason=reason,
        source_event_type=source_event_type,
        source_event_id=source_event_id,
    )
    session.add(decision)
    await session.flush()
    return decision
```

Add:

```python
async def latest_engagement_since(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    since: datetime,
) -> EngagementEvent | None:
    result = await session.execute(
        select(EngagementEvent)
        .where(
            EngagementEvent.profile_id == profile_id,
            EngagementEvent.occurred_at >= since,
        )
        .order_by(EngagementEvent.occurred_at.desc(), EngagementEvent.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
```

- [ ] **Step 4: Refactor daily maintenance**

In `app/scheduler/tasks.py`, stop joining `User`. Select active profiles only:

```python
result = await session.execute(select(UserProfile).where(UserProfile.search_active.is_(True)))
profiles = result.scalars().all()
```

For each profile:

```python
subscription = await get_subscription_snapshot(profile.user_id, session)
entitlements = effective_entitlements(subscription, now)
previous = {
    "search_active": profile.search_active,
    "search_expires_at": profile.search_expires_at.isoformat() if profile.search_expires_at else None,
}
if entitlements.paid_access:
    profile.search_expires_at = next_search_expiry(now, settings)
    profile.updated_at = now
    session.add(profile)
    searches_extended += 1
    await record_entitlement_decision(
        session,
        user_id=profile.user_id,
        profile_id=profile.id,
        decision_type="search_expiry_extended",
        reason="paid_entitlement",
        previous_value=previous,
        next_value={"search_expires_at": profile.search_expires_at.isoformat()},
    )
    continue
```

For free profiles:

```python
if profile.search_expires_at is None:
    profile.search_expires_at = next_search_expiry(now, settings)
    profile.updated_at = now
    session.add(profile)
    searches_extended += 1
    await record_entitlement_decision(... decision_type="search_expiry_seeded", reason="missing_expiry", ...)
    continue

latest = await latest_engagement_since(
    session,
    profile_id=profile.id,
    since=profile.search_expires_at - timedelta(days=settings.search_auto_pause_days),
)
if latest is not None:
    profile.search_expires_at = next_search_expiry(now, settings)
    profile.updated_at = now
    session.add(profile)
    searches_extended += 1
    await record_entitlement_decision(
        session,
        user_id=profile.user_id,
        profile_id=profile.id,
        decision_type="search_expiry_extended",
        reason="active_engagement",
        source_event_type=latest.event_type,
        source_event_id=latest.id,
        previous_value=previous,
        next_value={"search_expires_at": profile.search_expires_at.isoformat()},
    )
    continue

if profile.search_expires_at < now:
    profile.search_active = False
    profile.updated_at = now
    session.add(profile)
    searches_paused += 1
    await record_entitlement_decision(... decision_type="search_paused", reason="inactivity", ...)
```

- [ ] **Step 5: Run maintenance tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_scheduler_expiry.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/entitlements.py app/scheduler/tasks.py tests/integration/test_scheduler_expiry.py
git commit -m "feat: audit search entitlement decisions"
```

---

## Task 6: Frontend API Type and Settings Refactor

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/components/settings/SearchToggleSection.tsx`
- Modify: `frontend/src/pages/Settings.test.tsx`
- Modify: `frontend/src/components/settings/SearchToggleSection.test.tsx`
- Modify: `frontend/e2e/followed-companies.screenshot.spec.ts`
- Modify: `frontend/e2e/typeahead-dropdown.screenshot.spec.ts`

- [ ] **Step 1: Update frontend tests for new profile shape**

In profile fixtures, replace:

```ts
subscription: { plan: 'free', status: 'active', paid_active: false },
limits: { followed_companies: 5 },
```

with:

```ts
subscription: null,
entitlements: { paid_access: false, search_auto_pause: true },
limits: { followed_companies: 5 },
```

For paid fixtures use:

```ts
subscription: {
  tier: 'paid',
  status: 'active',
  current_period_end: '2026-06-27T00:00:00Z',
},
entitlements: { paid_access: true, search_auto_pause: false },
limits: { followed_companies: 100 },
```

- [ ] **Step 2: Run frontend tests to verify failure**

Run:

```bash
npm run test -- Settings.test.tsx SearchToggleSection.test.tsx
```

from `frontend/`.

Expected: FAIL because types/components still expect `subscription.paid_active`.

- [ ] **Step 3: Update TypeScript API types**

In `frontend/src/api/client.ts`, change:

```ts
subscription: SubscriptionInfo
```

to:

```ts
subscription: SubscriptionInfo | null
entitlements: EntitlementInfo
```

Use:

```ts
export interface SubscriptionInfo {
  tier: string
  status: 'active' | 'canceled' | 'expired' | 'refunded' | 'chargeback' | 'revoked'
  current_period_end: string
}

export interface EntitlementInfo {
  paid_access: boolean
  search_auto_pause: boolean
}
```

- [ ] **Step 4: Update settings usage**

In `frontend/src/pages/Settings.tsx`, change:

```tsx
paidActive={profile.subscription.paid_active}
```

to:

```tsx
searchAutoPause={profile.entitlements.search_auto_pause}
```

In `SearchToggleSection.tsx`, change props:

```ts
export interface SearchToggleSectionProps {
  active: boolean
  expiresAt: string | null
  searchAutoPause: boolean
}
```

Change render condition:

```tsx
{active && searchAutoPause && days != null && (
  <p className="text-xs text-muted mt-0.5">Auto-pause in {days} day{days === 1 ? '' : 's'}</p>
)}
```

- [ ] **Step 5: Run frontend verification**

Run from `frontend/`:

```bash
npm run test -- Settings.test.tsx SearchToggleSection.test.tsx
npm run typecheck
npm run build
```

Expected: PASS.

- [ ] **Step 6: Update screenshot fixtures**

Update `frontend/e2e/followed-companies.screenshot.spec.ts` and `frontend/e2e/typeahead-dropdown.screenshot.spec.ts` profile mocks to use the new `subscription`/`entitlements` shape. If any visible UI changes, regenerate PR screenshots before final PR update; if only API shape changed and visual output is unchanged, note no new screenshots are needed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/Settings.tsx frontend/src/components/settings/SearchToggleSection.tsx frontend/src/pages/Settings.test.tsx frontend/src/components/settings/SearchToggleSection.test.tsx frontend/e2e/followed-companies.screenshot.spec.ts frontend/e2e/typeahead-dropdown.screenshot.spec.ts
git commit -m "feat: consume effective entitlements in settings"
```

---

## Task 7: Full Verification and PR Hygiene

**Files:**
- Review: all changed files
- Update: PR body/screenshots only after user approval to push

- [ ] **Step 1: Run backend verification**

Run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run ruff check app tests
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/unit/test_entitlements.py -v
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/integration/test_subscription_entitlements_migration.py tests/integration/test_company_resolution_flow.py tests/integration/test_profile_service.py tests/integration/test_engagement_events.py tests/integration/test_onboarding_agent.py tests/integration/test_apply_lifecycle.py tests/integration/test_scheduler_expiry.py -v
UV_CACHE_DIR=/private/tmp/uv-cache uv run alembic heads
```

Expected: ruff PASS; pytest PASS; Alembic prints one head.

- [ ] **Step 2: Run frontend verification**

Run from `frontend/`:

```bash
npm run test
npm run typecheck
npm run build
```

Expected: PASS.

- [ ] **Step 3: Attempt E2E verification**

Run from `frontend/` only if Docker/local Postgres is available:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache npm run test:e2e -- --project=chromium
```

Expected: PASS. If local Docker/Postgres is unavailable, do not fake success; report the exact blocker and rely on GitHub Actions after the user allows pushing.

- [ ] **Step 4: Check branch state**

Run:

```bash
git status --short
git log --oneline --decorate -8
```

Expected: clean tree except known inaccessible `.env.example` status warning; latest commits are the implementation commits.

- [ ] **Step 5: Ask before pushing**

Because the user explicitly said not to push to the current branch/PR while the refactor is in progress, stop after local verification and ask for push approval. Do not run `git push` unless the user explicitly approves.
