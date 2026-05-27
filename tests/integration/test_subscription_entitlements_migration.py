"""Integration tests for subscription entitlement schema and backfill."""

import importlib.util
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import text

from alembic import command

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


def _reset_public_schema(sync_url: str) -> None:
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _fetch_all(sync_url: str, sql: str):
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql))
            return result.all()
    finally:
        engine.dispose()


def test_subscription_entitlement_migration_creates_canonical_schema(
    asyncpg_url, sync_url, monkeypatch
):
    _reset_public_schema(sync_url)
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)

    try:
        command.upgrade(Config("alembic.ini"), "head")

        user_subscription_columns = _fetch_all(
            sync_url,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users'
              AND column_name IN (
                'subscription_plan',
                'subscription_status',
                'subscription_current_period_end'
              )
            """,
        )
        assert user_subscription_columns == []

        tables = _fetch_all(
            sync_url,
            """
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
            ORDER BY table_name
            """,
        )
        assert {row.table_name for row in tables} == {
            "subscription_plans",
            "subscription_accounts",
            "subscriptions",
            "subscription_events",
            "engagement_events",
            "entitlement_decisions",
        }

        plans = _fetch_all(
            sync_url,
            """
            SELECT tier, followed_company_limit, valid_until
            FROM subscription_plans
            ORDER BY tier
            """,
        )
        assert [(row.tier, row.followed_company_limit, row.valid_until) for row in plans] == [
            ("free", 5, None),
            ("paid", 100, None),
        ]
    finally:
        _reset_public_schema(sync_url)


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
                id,
                user_id,
                remote_ok,
                search_active,
                search_expires_at,
                created_at,
                updated_at
            )
            VALUES
                (:active_profile_id, :active_user_id, true, true, NULL, now(), now()),
                (:paused_profile_id, :paused_user_id, true, false, NULL, now(), now()),
                (
                    :existing_profile_id,
                    :existing_user_id,
                    true,
                    true,
                    :existing_expiry,
                    now(),
                    now()
                )
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
