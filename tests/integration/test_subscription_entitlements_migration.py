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

CANONICAL_TABLES = {
    "subscription_plans",
    "subscription_accounts",
    "subscriptions",
    "subscription_events",
    "engagement_events",
    "entitlement_decisions",
}

EXPECTED_COLUMNS = {
    "subscription_plans": {
        "id": "NO",
        "tier": "NO",
        "display_name": "NO",
        "followed_company_limit": "NO",
        "valid_from": "NO",
        "valid_until": "YES",
        "created_at": "NO",
        "updated_at": "NO",
    },
    "subscription_accounts": {
        "id": "NO",
        "user_id": "NO",
        "provider": "NO",
        "provider_customer_id": "NO",
        "created_at": "NO",
        "updated_at": "NO",
    },
    "subscriptions": {
        "id": "NO",
        "user_id": "NO",
        "subscription_account_id": "NO",
        "plan_id": "NO",
        "provider": "NO",
        "provider_subscription_id": "NO",
        "status": "NO",
        "current_period_start": "NO",
        "current_period_end": "NO",
        "canceled_at": "YES",
        "ended_at": "YES",
        "created_at": "NO",
        "updated_at": "NO",
    },
    "subscription_events": {
        "id": "NO",
        "user_id": "NO",
        "subscription_id": "NO",
        "event_type": "NO",
        "provider": "NO",
        "provider_event_id": "NO",
        "occurred_at": "NO",
        "payload": "NO",
    },
    "engagement_events": {
        "id": "NO",
        "user_id": "NO",
        "profile_id": "NO",
        "event_type": "NO",
        "subject_type": "YES",
        "subject_id": "YES",
        "source": "NO",
        "occurred_at": "NO",
        "metadata": "NO",
    },
    "entitlement_decisions": {
        "id": "NO",
        "user_id": "NO",
        "profile_id": "NO",
        "decision_type": "NO",
        "previous_value": "YES",
        "next_value": "YES",
        "reason": "NO",
        "source_event_type": "YES",
        "source_event_id": "YES",
        "decided_at": "NO",
    },
}

EXPECTED_FOREIGN_KEYS = {
    ("subscription_accounts", "user_id", "users", "id"),
    ("subscriptions", "user_id", "users", "id"),
    ("subscriptions", "subscription_account_id", "subscription_accounts", "id"),
    ("subscriptions", "plan_id", "subscription_plans", "id"),
    ("subscription_events", "user_id", "users", "id"),
    ("subscription_events", "subscription_id", "subscriptions", "id"),
    ("engagement_events", "user_id", "users", "id"),
    ("engagement_events", "profile_id", "user_profiles", "id"),
    ("entitlement_decisions", "user_id", "users", "id"),
    ("entitlement_decisions", "profile_id", "user_profiles", "id"),
}

EXPECTED_CHECK_CONSTRAINTS = {
    "ck_subscriptions_status",
    "ck_subscription_events_event_type",
    "ck_engagement_events_event_type",
    "ck_entitlement_decisions_decision_type",
}

EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_subscription_plans_tier",
    "uq_subscription_accounts_provider_customer",
    "uq_subscriptions_provider_subscription",
    "uq_subscription_events_provider_event",
}

EXPECTED_INDEXES = {
    "ix_subscription_plans_tier",
    "ix_subscription_accounts_user_id",
    "ix_subscriptions_user_id",
    "ix_subscriptions_subscription_account_id",
    "ix_subscriptions_plan_id",
    "ix_subscriptions_provider",
    "ix_subscriptions_status",
    "ix_subscription_events_user_id",
    "ix_subscription_events_subscription_id",
    "ix_subscription_events_event_type",
    "ix_subscription_events_provider",
    "ix_subscription_events_occurred_at",
    "ix_engagement_events_user_id",
    "ix_engagement_events_profile_id",
    "ix_engagement_events_event_type",
    "ix_engagement_events_subject_id",
    "ix_engagement_events_source",
    "ix_engagement_events_occurred_at",
    "ix_engagement_events_profile_occurred_at",
    "ix_entitlement_decisions_user_id",
    "ix_entitlement_decisions_profile_id",
    "ix_entitlement_decisions_decision_type",
    "ix_entitlement_decisions_source_event_id",
    "ix_entitlement_decisions_decided_at",
}


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


def _canonical_tables(sync_url: str) -> set[str]:
    rows = _fetch_all(
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
        """,
    )
    return {row.table_name for row in rows}


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

        assert _canonical_tables(sync_url) == CANONICAL_TABLES

        columns = _fetch_all(
            sync_url,
            """
            SELECT table_name, column_name, is_nullable, data_type, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                'subscription_plans',
                'subscription_accounts',
                'subscriptions',
                'subscription_events',
                'engagement_events',
                'entitlement_decisions'
              )
            """,
        )
        columns_by_table = {
            table_name: {
                row.column_name: row
                for row in columns
                if row.table_name == table_name
            }
            for table_name in CANONICAL_TABLES
        }
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            actual_columns = columns_by_table[table_name]
            for column_name, is_nullable in expected_columns.items():
                assert actual_columns[column_name].is_nullable == is_nullable

        assert columns_by_table["subscription_events"]["payload"].data_type == "jsonb"
        assert columns_by_table["subscription_events"]["payload"].column_default == "'{}'::jsonb"
        assert columns_by_table["engagement_events"]["metadata"].data_type == "jsonb"
        assert columns_by_table["engagement_events"]["metadata"].column_default == "'{}'::jsonb"
        assert columns_by_table["entitlement_decisions"]["previous_value"].data_type == "jsonb"
        assert columns_by_table["entitlement_decisions"]["previous_value"].column_default is None
        assert columns_by_table["entitlement_decisions"]["next_value"].data_type == "jsonb"
        assert columns_by_table["entitlement_decisions"]["next_value"].column_default is None

        foreign_keys = _fetch_all(
            sync_url,
            """
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name IN (
                'subscription_plans',
                'subscription_accounts',
                'subscriptions',
                'subscription_events',
                'engagement_events',
                'entitlement_decisions'
              )
            """,
        )
        assert {
            (
                row.table_name,
                row.column_name,
                row.foreign_table_name,
                row.foreign_column_name,
            )
            for row in foreign_keys
        } == EXPECTED_FOREIGN_KEYS

        constraints = _fetch_all(
            sync_url,
            """
            SELECT conname, contype
            FROM pg_constraint
            WHERE conrelid::regclass::text IN (
                'subscription_plans',
                'subscription_accounts',
                'subscriptions',
                'subscription_events',
                'engagement_events',
                'entitlement_decisions'
            )
            """,
        )
        assert EXPECTED_CHECK_CONSTRAINTS <= {
            row.conname for row in constraints if row.contype == "c"
        }
        assert EXPECTED_UNIQUE_CONSTRAINTS <= {
            row.conname for row in constraints if row.contype == "u"
        }

        indexes = _fetch_all(
            sync_url,
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN (
                'subscription_plans',
                'subscription_accounts',
                'subscriptions',
                'subscription_events',
                'engagement_events',
                'entitlement_decisions'
              )
            """,
        )
        assert EXPECTED_INDEXES <= {row.indexname for row in indexes}

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

        command.downgrade(Config("alembic.ini"), "e4f5a6b7c8d9")
        assert _canonical_tables(sync_url) == set()
    finally:
        _reset_public_schema(sync_url)


@pytest.mark.asyncio
async def test_search_expiry_backfill_updates_only_active_null_expiry(db_session):
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
