"""Integration tests for subscription entitlement schema and backfill."""

import importlib.util
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "e1f2a3b4c5d6_add_subscription_entitlements.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("subscription_entitlements", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_user_subscription_columns_default_to_free_inactive_null(db_session):
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:user_id, :email, '', true, false, false)
            """
        ),
        {"user_id": user_id, "email": f"user-{user_id}@local"},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text(
                """
                SELECT subscription_plan, subscription_status, subscription_current_period_end
                FROM users
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    ).one()

    assert row.subscription_plan == "free"
    assert row.subscription_status == "inactive"
    assert row.subscription_current_period_end is None


@pytest.mark.asyncio
async def test_subscription_entitlement_backfill_updates_only_active_null_expiry(db_session):
    migration = _load_migration()
    active_user_id = uuid.uuid4()
    paused_user_id = uuid.uuid4()
    existing_user_id = uuid.uuid4()
    active_profile_id = uuid.uuid4()
    paused_profile_id = uuid.uuid4()
    existing_profile_id = uuid.uuid4()
    existing_expiry = datetime.now(UTC) + timedelta(days=30)

    for user_id in (active_user_id, paused_user_id, existing_user_id):
        await db_session.execute(
            text(
                """
                INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
                VALUES (:user_id, :email, '', true, false, false)
                """
            ),
            {"user_id": user_id, "email": f"user-{user_id}@local"},
        )

    await db_session.execute(
        text(
            """
            INSERT INTO user_profiles (
                id, user_id, search_active, search_expires_at, created_at, updated_at
            )
            VALUES
                (:active_profile_id, :active_user_id, true, NULL, now(), now()),
                (:paused_profile_id, :paused_user_id, false, NULL, now(), now()),
                (:existing_profile_id, :existing_user_id, true, :existing_expiry, now(), now())
            """
        ),
        {
            "active_profile_id": active_profile_id,
            "active_user_id": active_user_id,
            "paused_profile_id": paused_profile_id,
            "paused_user_id": paused_user_id,
            "existing_profile_id": existing_profile_id,
            "existing_user_id": existing_user_id,
            "existing_expiry": existing_expiry,
        },
    )
    await db_session.commit()

    before = datetime.now(UTC)
    await db_session.execute(text(migration.BACKFILL_ACTIVE_PROFILE_TRIAL_SQL))
    await db_session.commit()
    after = datetime.now(UTC)

    rows = (
        await db_session.execute(
            text(
                """
                SELECT id, search_expires_at
                FROM user_profiles
                WHERE id IN (:active_profile_id, :paused_profile_id, :existing_profile_id)
                """
            ),
            {
                "active_profile_id": active_profile_id,
                "paused_profile_id": paused_profile_id,
                "existing_profile_id": existing_profile_id,
            },
        )
    ).all()
    expiries = {row.id: row.search_expires_at for row in rows}

    active_expiry = expiries[active_profile_id]
    assert active_expiry is not None
    assert before + timedelta(days=7) <= active_expiry <= after + timedelta(days=7)
    assert expiries[paused_profile_id] is None
    assert expiries[existing_profile_id] == existing_expiry
