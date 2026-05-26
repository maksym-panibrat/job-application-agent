# Subscription Entitlements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add entitlements-only subscription behavior: account-level subscription fields, automatic search expiry lifecycle, and free/paid followed-company limits.

**Architecture:** Store subscription state on `users`, centralize entitlement decisions in `app/services/entitlements.py`, and keep `UserProfile` as the operational search/follow state. Backend APIs and onboarding call the same entitlement helper; frontend reads the effective limit from `GET /api/profile`.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy/Alembic, PostgreSQL, pytest/testcontainers, React, TanStack Query, Vitest/MSW.

---

## File Structure

- Create `app/services/entitlements.py`: pure entitlement helpers and the `CompanyFollowLimitError` domain error.
- Modify `app/models/user.py`: add `subscription_plan`, `subscription_status`, and `subscription_current_period_end`.
- Create `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py`: add user columns, check constraints, and active-profile expiry backfill.
- Modify `app/services/profile_service.py`: seed new profile expiry and validate company-follow updates.
- Modify `app/api/profile.py`: load current user for profile metadata, company-limit validation, and search pause/resume.
- Modify `app/scheduler/tasks.py`: extend paid-active search expiry, pause expired free search, and backfill legacy active null expiries.
- Modify `app/agents/onboarding.py`: enforce the same company limit when chat appends inferred companies.
- Modify `frontend/src/api/client.ts`: add `subscription` and `limits` to `Profile`.
- Modify `frontend/src/pages/Settings.tsx`: pass limits/subscription into settings components.
- Modify `frontend/src/components/settings/FollowedCompaniesSection.tsx`: display count/limit and block add at limit while keeping removals enabled.
- Modify `frontend/src/components/settings/SearchToggleSection.tsx`: hide auto-pause copy for paid-active users.
- Add/update backend tests in `tests/unit/test_entitlements.py`, `tests/integration/test_profile_service.py`, `tests/integration/test_company_resolution_flow.py`, `tests/integration/test_onboarding_agent.py`, `tests/integration/test_scheduler_expiry.py`, and a migration-shape test.
- Update frontend tests in `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`, `frontend/src/components/settings/SearchToggleSection.test.tsx`, and `frontend/src/pages/Settings.test.tsx`.

## Task 1: User Model And Migration

**Files:**
- Modify: `app/models/user.py`
- Create: `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py`
- Test: `tests/integration/test_subscription_entitlements_migration.py`

- [ ] **Step 1: Write the migration-shape test**

Create `tests/integration/test_subscription_entitlements_migration.py`:

```python
import uuid

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_users_have_subscription_defaults(db_session):
    user_id = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:id, :email, '', true, false, true)
        """),
        {"id": str(user_id), "email": f"sub-{user_id}@example.com"},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text("""
                SELECT subscription_plan, subscription_status, subscription_current_period_end
                FROM users
                WHERE id = :id
            """),
            {"id": str(user_id)},
        )
    ).one()

    assert row.subscription_plan == "free"
    assert row.subscription_status == "inactive"
    assert row.subscription_current_period_end is None


@pytest.mark.asyncio
async def test_active_null_expiry_backfill_sql_preserves_paused_profiles(db_session):
    active_user = uuid.uuid4()
    paused_user = uuid.uuid4()
    active_profile = uuid.uuid4()
    paused_profile = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES
              (:active_user, 'active-null@example.com', '', true, false, true),
              (:paused_user, 'paused-null@example.com', '', true, false, true)
        """),
        {"active_user": str(active_user), "paused_user": str(paused_user)},
    )
    await db_session.execute(
        text("""
            INSERT INTO user_profiles (
                id, user_id, remote_ok, search_active, search_expires_at, created_at, updated_at
            )
            VALUES
              (:active_profile, :active_user, true, true, NULL, NOW(), NOW()),
              (:paused_profile, :paused_user, true, false, NULL, NOW(), NOW())
        """),
        {
            "active_profile": str(active_profile),
            "active_user": str(active_user),
            "paused_profile": str(paused_profile),
            "paused_user": str(paused_user),
        },
    )
    await db_session.commit()

    await db_session.execute(
        text("""
            UPDATE user_profiles
            SET search_expires_at = NOW() + interval '7 days'
            WHERE search_active IS TRUE
              AND search_expires_at IS NULL
        """)
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            text("""
                SELECT id, search_active, search_expires_at
                FROM user_profiles
                WHERE id IN (:active_profile, :paused_profile)
                ORDER BY search_active DESC
            """),
            {"active_profile": str(active_profile), "paused_profile": str(paused_profile)},
        )
    ).all()

    assert rows[0].search_active is True
    assert rows[0].search_expires_at is not None
    assert rows[1].search_active is False
    assert rows[1].search_expires_at is None
```

- [ ] **Step 2: Run the migration-shape test to verify it fails**

Run:

```bash
uv run pytest tests/integration/test_subscription_entitlements_migration.py -v
```

Expected: the first test fails because `users.subscription_plan` does not exist.

- [ ] **Step 3: Add user model fields**

In `app/models/user.py`, add imports and fields:

```python
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column
```

Inside `class User` after `is_verified`:

```python
    subscription_plan: str = Field(
        default="free",
        sa_column=Column(sa.String, nullable=False, server_default="free"),
    )
    subscription_status: str = Field(
        default="inactive",
        sa_column=Column(sa.String, nullable=False, server_default="inactive"),
    )
    subscription_current_period_end: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
```

- [ ] **Step 4: Add Alembic migration**

Create `alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py`:

```python
"""add subscription entitlements

Revision ID: e1f2a3b4c5d6
Revises: 5a6b7c8d9e0f
Create Date: 2026-05-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "5a6b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("subscription_plan", sa.String(), nullable=False, server_default="free"),
    )
    op.add_column(
        "users",
        sa.Column("subscription_status", sa.String(), nullable=False, server_default="inactive"),
    )
    op.add_column(
        "users",
        sa.Column("subscription_current_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_subscription_plan",
        "users",
        "subscription_plan IN ('free', 'paid')",
    )
    op.create_check_constraint(
        "ck_users_subscription_status",
        "users",
        "subscription_status IN ('inactive', 'active')",
    )
    op.execute(
        """
        UPDATE user_profiles
        SET search_expires_at = NOW() + interval '7 days'
        WHERE search_active IS TRUE
          AND search_expires_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_subscription_status", "users", type_="check")
    op.drop_constraint("ck_users_subscription_plan", "users", type_="check")
    op.drop_column("users", "subscription_current_period_end")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "subscription_plan")
```

- [ ] **Step 5: Run migration/model tests**

Run:

```bash
uv run pytest tests/integration/test_subscription_entitlements_migration.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/models/user.py alembic/versions/e1f2a3b4c5d6_add_subscription_entitlements.py tests/integration/test_subscription_entitlements_migration.py
git commit -m "feat: add subscription entitlement fields"
```

## Task 2: Entitlement Helper

**Files:**
- Create: `app/services/entitlements.py`
- Test: `tests/unit/test_entitlements.py`

- [ ] **Step 1: Write entitlement unit tests**

Create `tests/unit/test_entitlements.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.user import User
from app.services.entitlements import (
    CompanyFollowLimitError,
    company_follow_limit,
    dedupe_company_ids,
    is_paid_active,
    next_search_expiry,
    validate_company_follow_change,
)


class SettingsStub:
    search_auto_pause_days = 7


def user(plan="free", status="inactive", period_end=None):
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        subscription_plan=plan,
        subscription_status=status,
        subscription_current_period_end=period_end,
    )


def ids(count):
    return [uuid.uuid4() for _ in range(count)]


def test_paid_active_requires_paid_plan_and_active_status():
    assert is_paid_active(user("paid", "active")) is True
    assert is_paid_active(user("paid", "inactive")) is False
    assert is_paid_active(user("free", "active")) is False


def test_current_period_end_is_metadata_only():
    expired = datetime.now(UTC) - timedelta(days=30)
    assert is_paid_active(user("paid", "active", period_end=expired)) is True


def test_company_limits():
    assert company_follow_limit(user()) == 5
    assert company_follow_limit(user("paid", "active")) == 100


def test_next_search_expiry_uses_settings_days():
    now = datetime(2026, 5, 26, tzinfo=UTC)
    assert next_search_expiry(now, SettingsStub()) == now + timedelta(days=7)


def test_dedupe_company_ids_preserves_order_and_accepts_strings():
    first = uuid.uuid4()
    second = uuid.uuid4()
    assert dedupe_company_ids([str(first), first, str(second)]) == [first, second]


def test_free_user_can_follow_five():
    requested = ids(5)
    assert validate_company_follow_change(user(), [], requested) == requested


def test_free_user_cannot_follow_six():
    with pytest.raises(CompanyFollowLimitError) as exc:
        validate_company_follow_change(user(), [], ids(6))
    assert "Free accounts can follow up to 5 companies" in str(exc.value)


def test_paid_user_can_follow_one_hundred():
    requested = ids(100)
    assert validate_company_follow_change(user("paid", "active"), [], requested) == requested


def test_downgraded_user_can_remove_without_reaching_limit():
    current = ids(10)
    requested = current[:9]
    assert validate_company_follow_change(user(), current, requested) == requested


def test_downgraded_user_cannot_swap_new_company_while_over_limit():
    current = ids(10)
    requested = current[:8] + [uuid.uuid4()]
    with pytest.raises(CompanyFollowLimitError):
        validate_company_follow_change(user(), current, requested)
```

- [ ] **Step 2: Run entitlement tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_entitlements.py -v
```

Expected: import failure for `app.services.entitlements`.

- [ ] **Step 3: Implement entitlement helper**

Create `app/services/entitlements.py`:

```python
import uuid
from datetime import datetime, timedelta
from typing import Iterable

from app.models.user import User

FREE_COMPANY_LIMIT = 5
PAID_COMPANY_LIMIT = 100
FREE_PLAN = "free"
PAID_PLAN = "paid"
ACTIVE_STATUS = "active"
INACTIVE_STATUS = "inactive"


class CompanyFollowLimitError(ValueError):
    def __init__(self, limit: int):
        self.limit = limit
        label = "Paid accounts" if limit == PAID_COMPANY_LIMIT else "Free accounts"
        super().__init__(f"{label} can follow up to {limit} companies.")


def is_paid_active(user: User) -> bool:
    return user.subscription_plan == PAID_PLAN and user.subscription_status == ACTIVE_STATUS


def company_follow_limit(user: User) -> int:
    return PAID_COMPANY_LIMIT if is_paid_active(user) else FREE_COMPANY_LIMIT


def next_search_expiry(now: datetime, settings) -> datetime:
    return now + timedelta(days=settings.search_auto_pause_days)


def dedupe_company_ids(company_ids: Iterable[uuid.UUID | str]) -> list[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    result: list[uuid.UUID] = []
    for raw in company_ids:
        company_id = uuid.UUID(raw) if isinstance(raw, str) else raw
        if company_id in seen:
            continue
        seen.add(company_id)
        result.append(company_id)
    return result


def validate_company_follow_change(
    user: User,
    current_ids: Iterable[uuid.UUID | str],
    requested_ids: Iterable[uuid.UUID | str],
) -> list[uuid.UUID]:
    current = dedupe_company_ids(current_ids)
    requested = dedupe_company_ids(requested_ids)
    limit = company_follow_limit(user)

    if len(requested) <= limit:
        return requested

    current_set = set(current)
    requested_set = set(requested)
    introduced = requested_set - current_set
    removal_only = requested_set < current_set
    if removal_only and not introduced:
        return requested

    raise CompanyFollowLimitError(limit)
```

- [ ] **Step 4: Run entitlement tests**

Run:

```bash
uv run pytest tests/unit/test_entitlements.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/entitlements.py tests/unit/test_entitlements.py
git commit -m "feat: add entitlement policy helper"
```

## Task 3: Profile Creation And Profile API Enforcement

**Files:**
- Modify: `app/services/profile_service.py`
- Modify: `app/api/profile.py`
- Test: `tests/integration/test_profile_service.py`
- Test: `tests/integration/test_company_resolution_flow.py`

- [ ] **Step 1: Add profile creation expiry test**

Append to `tests/integration/test_profile_service.py`:

```python
from datetime import UTC, datetime, timedelta


@pytest.mark.asyncio
async def test_get_or_create_profile_sets_initial_search_expiry(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"expiry-{uuid.uuid4()}@test.com",
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
    assert before + timedelta(days=6, hours=23) <= profile.search_expires_at
    assert profile.search_expires_at <= after + timedelta(days=7, minutes=1)
```

- [ ] **Step 2: Add profile API limit tests**

Append to `tests/integration/test_company_resolution_flow.py`:

```python
import uuid

from app.models.company import Company
from app.models.user import User
from app.models.user_profile import UserProfile
```

Add helper and tests:

```python
async def _companies(db_session, count: int) -> list[Company]:
    rows = []
    for i in range(count):
        company = Company(
            canonical_name=f"Company {i}",
            normalized_key=f"company-{uuid.uuid4()}",
            provider_slugs={"greenhouse": f"company-{uuid.uuid4()}"},
        )
        db_session.add(company)
        rows.append(company)
    await db_session.commit()
    for company in rows:
        await db_session.refresh(company)
    return rows


@pytest.mark.asyncio
async def test_profile_patch_rejects_sixth_free_company(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    companies = await _companies(db_session, 6)
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(c.id) for c in companies]},
            headers=auth_headers,
        )

    assert resp.status_code == 422
    assert "Free accounts can follow up to 5 companies" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_profile_patch_allows_paid_user_to_follow_more_than_five(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    user, _ = seeded_user
    user.subscription_plan = "paid"
    user.subscription_status = "active"
    db_session.add(user)
    await db_session.commit()
    companies = await _companies(db_session, 6)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(c.id) for c in companies]},
            headers=auth_headers,
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_patch_limit_failure_does_not_persist_other_fields(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    _, profile = seeded_user
    companies = await _companies(db_session, 6)
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={
                "full_name": "Should Not Persist",
                "target_company_ids": [str(c.id) for c in companies],
            },
            headers=auth_headers,
        )

    assert resp.status_code == 422
    await db_session.refresh(profile)
    assert profile.full_name != "Should Not Persist"


@pytest.mark.asyncio
async def test_profile_patch_allows_downgraded_user_removal_only_subset(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    _, profile = seeded_user
    companies = await _companies(db_session, 10)
    profile.target_company_ids = [c.id for c in companies]
    db_session.add(profile)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(c.id) for c in companies[:9]]},
            headers=auth_headers,
        )

    assert resp.status_code == 200
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_profile_service.py::test_get_or_create_profile_sets_initial_search_expiry tests/integration/test_company_resolution_flow.py -v
```

Expected: expiry test fails with `search_expires_at is None`; company-limit tests fail because no enforcement exists.

- [ ] **Step 4: Implement profile creation expiry**

In `app/services/profile_service.py`, add imports:

```python
from datetime import UTC, datetime

from app.config import get_settings
from app.models.user import User
from app.services.entitlements import next_search_expiry
```

In `get_or_create_profile`, replace create block with:

```python
    if profile is None:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError(f"user {user_id} not found")
        now = datetime.now(UTC)
        profile = UserProfile(
            user_id=user_id,
            search_active=True,
            search_expires_at=next_search_expiry(now, get_settings()),
        )
```

- [ ] **Step 5: Implement profile PATCH limit enforcement**

In `app/api/profile.py`, import current user and entitlement error:

```python
from app.api.deps import get_current_profile, get_current_user
from app.models.user import User
from app.services.entitlements import CompanyFollowLimitError
```

Change `update_profile` signature:

```python
async def update_profile(
    data: dict,
    user: User = Depends(get_current_user),
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
```

Before calling `profile_service.update_profile`, add:

```python
    try:
        updated = await profile_service.update_profile(
            profile.id,
            filtered,
            session,
            user=user,
        )
    except CompanyFollowLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": str(updated.id), "updated": True}
```

Remove the old `updated = ...` and return lines.

In `app/services/profile_service.py`, import helper:

```python
from app.services.entitlements import validate_company_follow_change
```

Change signature and target-company block:

```python
async def update_profile(
    profile_id: uuid.UUID,
    data: dict,
    session: AsyncSession,
    *,
    user: User | None = None,
) -> UserProfile:
    profile = await session.get(UserProfile, profile_id)
    if profile is None:
        raise ValueError(f"profile {profile_id} not found")
    if "target_company_ids" in data:
        if user is None:
            user = await session.get(User, profile.user_id)
        if user is None:
            raise ValueError(f"user {profile.user_id} not found")
        raw = data["target_company_ids"]
        if not isinstance(raw, list):
            raise ValueError("target_company_ids must be a list")
        data["target_company_ids"] = validate_company_follow_change(
            user,
            profile.target_company_ids or [],
            raw,
        )
```

- [ ] **Step 6: Run profile tests**

Run:

```bash
uv run pytest tests/integration/test_profile_service.py tests/integration/test_company_resolution_flow.py -v
```

Expected: tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/profile_service.py app/api/profile.py tests/integration/test_profile_service.py tests/integration/test_company_resolution_flow.py
git commit -m "feat: enforce company follow limits in profile API"
```

## Task 4: Search Toggle And Daily Maintenance

**Files:**
- Modify: `app/api/profile.py`
- Modify: `app/scheduler/tasks.py`
- Test: `tests/e2e/test_core_workflow.py`
- Test: `tests/integration/test_scheduler_expiry.py`
- Test: `tests/smoke/test_search_toggle.py`

- [ ] **Step 1: Update search-toggle expectations**

In `tests/e2e/test_core_workflow.py::test_toggle_search_pause`, add:

```python
    assert resp.json()["search_expires_at"] is None
```

In `tests/smoke/test_search_toggle.py`, after pausing, assert:

```python
    assert resp.json()["search_expires_at"] is None
```

- [ ] **Step 2: Add maintenance paid/null-expiry tests**

Append to `tests/integration/test_scheduler_expiry.py`:

```python
@pytest.mark.asyncio
async def test_paid_active_expired_search_extended_by_maintenance(db_session):
    profile = await _create_user_and_profile(
        db_session, search_active=True, expires_delta=timedelta(hours=-1)
    )
    user = await db_session.get(User, profile.user_id)
    user.subscription_plan = "paid"
    user.subscription_status = "active"
    db_session.add(user)
    await db_session.commit()

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at > datetime.now(UTC) + timedelta(days=6)


@pytest.mark.asyncio
async def test_active_free_null_expiry_gets_fresh_window(db_session):
    profile = await _create_user_and_profile(db_session, search_active=True, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at > datetime.now(UTC) + timedelta(days=6)


@pytest.mark.asyncio
async def test_inactive_null_expiry_stays_paused(db_session):
    profile = await _create_user_and_profile(db_session, search_active=False, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is False
    assert profile.search_expires_at is None
```

Update the existing `test_no_expiry_not_paused` name/assertion to match the new behavior:

```python
async def test_active_no_expiry_receives_new_expiry(db_session):
    profile = await _create_user_and_profile(db_session, search_active=True, expires_delta=None)

    await run_daily_maintenance()

    await db_session.refresh(profile)
    assert profile.search_active is True
    assert profile.search_expires_at is not None
```

- [ ] **Step 3: Run search lifecycle tests to verify failures**

Run:

```bash
uv run pytest tests/integration/test_scheduler_expiry.py tests/e2e/test_core_workflow.py::test_toggle_search_pause -v
```

Expected: pause endpoint still returns old expiry behavior or lacks paid extension.

- [ ] **Step 4: Update search toggle endpoint**

In `app/api/profile.py::toggle_search`, change pause/resume update construction:

```python
    from datetime import datetime

    from app.services.entitlements import next_search_expiry

    search_active = data.get("search_active", True)
    updates: dict = {"search_active": search_active}
    settings = get_settings()
    if search_active:
        updates["search_expires_at"] = next_search_expiry(datetime.now(UTC), settings)
    else:
        updates["search_expires_at"] = None
```

Also update `profile_service.update_profile` so `None` can be assigned for `search_expires_at` without changing existing PATCH behavior for other nullable profile fields. Replace:

```python
        if hasattr(profile, key) and value is not None:
            setattr(profile, key, value)
```

with:

```python
        if hasattr(profile, key) and (value is not None or key == "search_expires_at"):
            setattr(profile, key, value)
```

- [ ] **Step 5: Update daily maintenance**

In `app/scheduler/tasks.py`, import `User`, `is_paid_active`, and `next_search_expiry` inside `run_daily_maintenance`:

```python
    from app.models.user import User
    from app.services.entitlements import is_paid_active, next_search_expiry
```

Replace the auto-pause query and loop with:

```python
        result = await session.execute(
            select(UserProfile, User)
            .join(User, User.id == UserProfile.user_id)
            .where(UserProfile.search_active.is_(True))
        )
        active_profile_rows = result.all()
        searches_paused = 0
        searches_extended = 0
        now = datetime.now(UTC)
        for profile, user in active_profile_rows:
            if is_paid_active(user):
                profile.search_expires_at = next_search_expiry(now, settings)
                profile.updated_at = now
                session.add(profile)
                searches_extended += 1
                await log.ainfo(
                    "maintenance.search_extended",
                    profile_id=str(profile.id),
                    user_id=str(user.id),
                )
                continue

            if profile.search_expires_at is None:
                profile.search_expires_at = next_search_expiry(now, settings)
                profile.updated_at = now
                session.add(profile)
                searches_extended += 1
                await log.ainfo(
                    "maintenance.search_expiry_seeded",
                    profile_id=str(profile.id),
                    user_id=str(user.id),
                )
                continue

            if profile.search_expires_at < now:
                profile.search_active = False
                profile.updated_at = now
                session.add(profile)
                searches_paused += 1
                await log.awarning(
                    "maintenance.search_paused",
                    profile_id=str(profile.id),
                    user_id=str(user.id),
                )
        if searches_paused or searches_extended:
            await session.commit()
            await log.ainfo(
                "maintenance.searches_reconciled",
                paused=searches_paused,
                extended=searches_extended,
            )
```

Update the return value:

```python
        "searches_paused": searches_paused,
```

- [ ] **Step 6: Run lifecycle tests**

Run:

```bash
uv run pytest tests/integration/test_scheduler_expiry.py tests/e2e/test_core_workflow.py::test_toggle_search_pause tests/smoke/test_search_toggle.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/api/profile.py app/services/profile_service.py app/scheduler/tasks.py tests/integration/test_scheduler_expiry.py tests/e2e/test_core_workflow.py tests/smoke/test_search_toggle.py
git commit -m "feat: apply subscription-aware search expiry"
```

## Task 5: API Metadata And Onboarding Enforcement

**Files:**
- Modify: `app/api/profile.py`
- Modify: `app/agents/onboarding.py`
- Test: `tests/integration/test_company_resolution_flow.py`
- Test: `tests/integration/test_onboarding_agent.py`

- [ ] **Step 1: Add profile metadata API test**

Append to `tests/integration/test_company_resolution_flow.py`:

```python
@pytest.mark.asyncio
async def test_get_profile_includes_subscription_and_limits(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"] == {
        "plan": "free",
        "status": "inactive",
        "paid_active": False,
    }
    assert body["limits"] == {"followed_companies": 5}
```

- [ ] **Step 2: Add onboarding limit tests**

Append to `tests/integration/test_onboarding_agent.py` near existing `persist_inferred_companies` tests:

```python
@pytest.mark.asyncio
async def test_persist_inferred_companies_respects_free_limit(db_session, monkeypatch):
    from app.agents.onboarding import persist_inferred_companies
    from app.services import company_resolver
    from app.services.entitlements import CompanyFollowLimitError

    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"limit-{user_id}@local")
    db_session.add(user)
    existing = []
    for i in range(5):
        company = Company(
            canonical_name=f"Existing {i}",
            normalized_key=f"existing-{uuid.uuid4()}",
            provider_slugs={"greenhouse": f"existing-{uuid.uuid4()}"},
        )
        db_session.add(company)
        existing.append(company)
    await db_session.commit()
    profile = UserProfile(user_id=user_id, target_company_ids=[c.id for c in existing])
    db_session.add(profile)
    await db_session.commit()

    new_company = Company(
        canonical_name="Overflow",
        normalized_key=f"overflow-{uuid.uuid4()}",
        provider_slugs={"greenhouse": f"overflow-{uuid.uuid4()}"},
    )
    db_session.add(new_company)
    await db_session.commit()

    async def fake_resolve(name, session):
        return new_company

    monkeypatch.setattr(company_resolver, "resolve", fake_resolve)

    with pytest.raises(CompanyFollowLimitError):
        await persist_inferred_companies(profile, ["Overflow"], db_session)
```

- [ ] **Step 3: Run tests to verify failures**

Run:

```bash
uv run pytest tests/integration/test_company_resolution_flow.py::test_get_profile_includes_subscription_and_limits tests/integration/test_onboarding_agent.py::test_persist_inferred_companies_respects_free_limit -v
```

Expected: profile metadata keys missing; onboarding does not enforce the limit.

- [ ] **Step 4: Add profile metadata**

In `app/api/profile.py`, import:

```python
from app.services.entitlements import company_follow_limit, is_paid_active
```

Change `get_profile` signature:

```python
async def get_profile(
    user: User = Depends(get_current_user),
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
```

Add to returned dict:

```python
        "subscription": {
            "plan": user.subscription_plan,
            "status": user.subscription_status,
            "paid_active": is_paid_active(user),
        },
        "limits": {
            "followed_companies": company_follow_limit(user),
        },
```

- [ ] **Step 5: Enforce onboarding append limit**

In `app/agents/onboarding.py`, import:

```python
from app.models.user import User
from app.services.entitlements import validate_company_follow_change
```

In `persist_inferred_companies`, after building `resolved_ids` and before assigning:

```python
    user = await session.get(User, profile.user_id)
    if user is None:
        raise ValueError(f"user {profile.user_id} not found")
    resolved_ids = validate_company_follow_change(
        user,
        profile.target_company_ids or [],
        resolved_ids,
    )
```

- [ ] **Step 6: Run metadata and onboarding tests**

Run:

```bash
uv run pytest tests/integration/test_company_resolution_flow.py tests/integration/test_onboarding_agent.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/api/profile.py app/agents/onboarding.py tests/integration/test_company_resolution_flow.py tests/integration/test_onboarding_agent.py
git commit -m "feat: expose and enforce subscription entitlements"
```

## Task 6: Frontend Limit Display And Search Copy

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/components/settings/FollowedCompaniesSection.tsx`
- Modify: `frontend/src/components/settings/SearchToggleSection.tsx`
- Test: `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`
- Test: `frontend/src/components/settings/SearchToggleSection.test.tsx`
- Test: `frontend/src/pages/Settings.test.tsx`

- [ ] **Step 1: Add frontend tests**

In `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`, update existing renders to pass `limit={5}` or set a default prop after component changes. Add:

```tsx
it('shows count and limit', () => {
  render(withCtx(
    <FollowedCompaniesSection
      companies={[{ id: 'a', canonical_name: 'Linear' }]}
      limit={5}
    />
  ))
  expect(screen.getByText(/1 of 5/i)).toBeInTheDocument()
})

it('blocks adding when at the limit but keeps remove enabled', async () => {
  render(withCtx(
    <FollowedCompaniesSection
      companies={[{ id: 'a', canonical_name: 'Linear' }]}
      limit={1}
    />
  ))
  expect(screen.getByPlaceholderText(/company limit reached/i)).toBeDisabled()
  expect(screen.getByLabelText(/Remove Linear/i)).toBeEnabled()
})
```

In `frontend/src/components/settings/SearchToggleSection.test.tsx`, add:

```tsx
it('hides auto-pause copy for paid active users', () => {
  const expires = new Date(Date.now() + 7 * 86_400_000).toISOString()
  render(withCtx(
    <SearchToggleSection active={true} expiresAt={expires} paidActive={true} />
  ))
  expect(screen.queryByText(/Auto-pause/i)).not.toBeInTheDocument()
})
```

In `frontend/src/pages/Settings.test.tsx`, add `subscription` and `limits` to `fullProfile()`:

```tsx
    subscription: { plan: 'free', status: 'inactive', paid_active: false },
    limits: { followed_companies: 5 },
```

- [ ] **Step 2: Run frontend tests to verify failures**

Run:

```bash
cd frontend
npm run test -- FollowedCompaniesSection.test.tsx SearchToggleSection.test.tsx Settings.test.tsx
```

Expected: TypeScript/prop failures until components and API types are updated.

- [ ] **Step 3: Update API types**

In `frontend/src/api/client.ts`, add:

```ts
export interface SubscriptionInfo {
  plan: 'free' | 'paid'
  status: 'inactive' | 'active'
  paid_active: boolean
}

export interface ProfileLimits {
  followed_companies: number
}
```

Add fields to `Profile`:

```ts
  subscription: SubscriptionInfo
  limits: ProfileLimits
```

- [ ] **Step 4: Wire Settings props**

In `frontend/src/pages/Settings.tsx`, change:

```tsx
      <SearchToggleSection active={profile.search_active} expiresAt={profile.search_expires_at} />
```

to:

```tsx
      <SearchToggleSection
        active={profile.search_active}
        expiresAt={profile.search_expires_at}
        paidActive={profile.subscription.paid_active}
      />
```

Change:

```tsx
      <FollowedCompaniesSection companies={profile.target_companies ?? []} />
```

to:

```tsx
      <FollowedCompaniesSection
        companies={profile.target_companies ?? []}
        limit={profile.limits.followed_companies}
      />
```

- [ ] **Step 5: Update FollowedCompaniesSection**

Change props:

```tsx
export interface FollowedCompaniesSectionProps {
  companies: Company[]
  limit: number
}

export function FollowedCompaniesSection({ companies, limit }: FollowedCompaniesSectionProps) {
```

Add:

```tsx
  const atLimit = optimistic.length >= limit
```

Below the section description, add:

```tsx
        <p className="text-xs text-muted">
          {optimistic.length} of {limit} followed companies
        </p>
```

At start of `commit`:

```tsx
    if (optimistic.length >= limit) {
      setError(`You can follow up to ${limit} companies.`)
      return
    }
```

Update the input:

```tsx
            placeholder={atLimit ? 'Company limit reached' : 'Add a company you want to follow'}
            disabled={busy || atLimit}
```

- [ ] **Step 6: Update SearchToggleSection**

In `frontend/src/components/settings/SearchToggleSection.tsx`, update props:

```tsx
export interface SearchToggleSectionProps {
  active: boolean
  expiresAt: string | null
  paidActive?: boolean
}
```

Change function signature:

```tsx
export function SearchToggleSection({ active, expiresAt, paidActive = false }: SearchToggleSectionProps) {
```

Change copy condition:

```tsx
          {active && !paidActive && days != null && (
```

- [ ] **Step 7: Run frontend tests**

Run:

```bash
cd frontend
npm run test -- FollowedCompaniesSection.test.tsx SearchToggleSection.test.tsx Settings.test.tsx
npm run typecheck
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/Settings.tsx frontend/src/components/settings/FollowedCompaniesSection.tsx frontend/src/components/settings/SearchToggleSection.tsx frontend/src/components/settings/FollowedCompaniesSection.test.tsx frontend/src/components/settings/SearchToggleSection.test.tsx frontend/src/pages/Settings.test.tsx
git commit -m "feat: show subscription follow limits in settings"
```

## Task 7: Final Verification And Docs

**Files:**
- Modify: `README.md` if manual entitlement operation needs a short note.
- Verify: backend and frontend targeted suites.

- [ ] **Step 1: Add optional operator note**

If no existing operator doc has a better place, add this to `README.md` under operational notes:

````markdown
### Manual subscription entitlement

Until billing integration lands, subscription entitlement is controlled by fields on `users`.

```sql
UPDATE users
SET subscription_plan = 'paid',
    subscription_status = 'active'
WHERE email = 'user@example.com';
```

Set `subscription_status = 'inactive'` to return an account to free limits. The optional `subscription_current_period_end` field is metadata only until billing sync exists.
````

- [ ] **Step 2: Run backend verification**

Run:

```bash
uv run pytest tests/unit/test_entitlements.py tests/integration/test_profile_service.py tests/integration/test_company_resolution_flow.py tests/integration/test_onboarding_agent.py tests/integration/test_scheduler_expiry.py tests/integration/test_subscription_entitlements_migration.py -v
```

Expected: all pass.

- [ ] **Step 3: Run frontend verification**

Run:

```bash
cd frontend
npm run test -- FollowedCompaniesSection.test.tsx SearchToggleSection.test.tsx Settings.test.tsx
npm run typecheck
```

Expected: all pass.

- [ ] **Step 4: Check formatting**

Run:

```bash
uv run ruff check app tests
```

Expected: no lint errors.

- [ ] **Step 5: Inspect changed files**

Run:

```bash
git status --short
git diff --stat
```

Expected: only subscription entitlement implementation, tests, migration, and optional README changes.

- [ ] **Step 6: Commit remaining docs if changed**

```bash
git add README.md
git commit -m "docs: document manual subscription entitlement"
```

Skip this commit if `README.md` was not changed.

## Self-Review Notes

- Spec coverage: account fields, metadata-only current period end, profile creation expiry, pause/resume, paid maintenance extension, legacy null-expiry handling, company limits, downgrade cleanup, onboarding, frontend display, and tests are all mapped to tasks.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" steps are required.
- Type consistency: API fields use `subscription.plan`, `subscription.status`, `subscription.paid_active`, and `limits.followed_companies` consistently across backend and frontend tasks.
