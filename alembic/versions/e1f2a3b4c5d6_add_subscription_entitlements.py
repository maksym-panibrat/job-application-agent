"""add subscription entitlements

Revision ID: e1f2a3b4c5d6
Revises: e4f5a6b7c8d9
Create Date: 2026-05-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL_ACTIVE_PROFILE_TRIAL_SQL = """
UPDATE user_profiles
SET search_expires_at = NOW() + interval '7 days'
WHERE search_active IS TRUE
  AND search_expires_at IS NULL
"""


SEED_SUBSCRIPTION_PLANS_SQL = """
INSERT INTO subscription_plans (
    id,
    tier,
    display_name,
    followed_company_limit,
    valid_from,
    valid_until,
    created_at,
    updated_at
)
VALUES
    (gen_random_uuid(), 'free', 'Free', 5, NOW(), NULL, NOW(), NOW()),
    (gen_random_uuid(), 'paid', 'Paid', 100, NOW(), NULL, NOW(), NOW())
ON CONFLICT (tier) DO NOTHING
"""


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("followed_company_limit", sa.Integer(), nullable=False),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tier", name="uq_subscription_plans_tier"),
    )
    op.create_index("ix_subscription_plans_tier", "subscription_plans", ["tier"], unique=False)

    op.create_table(
        "subscription_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_customer_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_customer_id", name="uq_subscription_accounts_provider_customer"
        ),
    )
    op.create_index(
        "ix_subscription_accounts_user_id", "subscription_accounts", ["user_id"], unique=False
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subscription_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','canceled','expired','refunded','chargeback','revoked')",
            name="ck_subscriptions_status",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"]),
        sa.ForeignKeyConstraint(["subscription_account_id"], ["subscription_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_subscription_id", name="uq_subscriptions_provider_subscription"
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=False)
    op.create_index(
        "ix_subscriptions_subscription_account_id",
        "subscriptions",
        ["subscription_account_id"],
        unique=False,
    )
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"], unique=False)

    op.create_table(
        "subscription_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'subscription_created',"
            "'subscription_renewed',"
            "'subscription_canceled',"
            "'subscription_expired',"
            "'subscription_refunded',"
            "'subscription_chargeback',"
            "'subscription_revoked',"
            "'subscription_reactivated',"
            "'subscription_plan_changed'"
            ")",
            name="ck_subscription_events_event_type",
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_subscription_events_provider"
        ),
    )
    op.create_index(
        "ix_subscription_events_user_id", "subscription_events", ["user_id"], unique=False
    )
    op.create_index(
        "ix_subscription_events_subscription_id",
        "subscription_events",
        ["subscription_id"],
        unique=False,
    )

    op.create_table(
        "engagement_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.Text(), server_default=sa.text("'api'"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'company_followed',"
            "'company_unfollowed',"
            "'profile_updated',"
            "'resume_uploaded',"
            "'application_dismissed',"
            "'application_applied',"
            "'chat_message_sent',"
            "'search_resumed'"
            ")",
            name="ck_engagement_events_event_type",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engagement_events_user_id", "engagement_events", ["user_id"], unique=False)
    op.create_index(
        "ix_engagement_events_profile_id", "engagement_events", ["profile_id"], unique=False
    )

    op.create_table(
        "entitlement_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_event_type", sa.Text(), nullable=True),
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_type IN ("
            "'follow_limit_applied',"
            "'follow_limit_rejected',"
            "'subscription_plan_rejected',"
            "'search_expiry_seeded',"
            "'search_expiry_extended',"
            "'search_paused',"
            "'paid_entitlement_activated',"
            "'paid_entitlement_ended',"
            "'over_limit_companies_preserved'"
            ")",
            name="ck_entitlement_decisions_decision_type",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entitlement_decisions_user_id", "entitlement_decisions", ["user_id"], unique=False
    )
    op.create_index(
        "ix_entitlement_decisions_profile_id",
        "entitlement_decisions",
        ["profile_id"],
        unique=False,
    )

    op.execute(SEED_SUBSCRIPTION_PLANS_SQL)
    op.execute(BACKFILL_ACTIVE_PROFILE_TRIAL_SQL)


def downgrade() -> None:
    op.drop_index("ix_entitlement_decisions_profile_id", table_name="entitlement_decisions")
    op.drop_index("ix_entitlement_decisions_user_id", table_name="entitlement_decisions")
    op.drop_table("entitlement_decisions")
    op.drop_index("ix_engagement_events_profile_id", table_name="engagement_events")
    op.drop_index("ix_engagement_events_user_id", table_name="engagement_events")
    op.drop_table("engagement_events")
    op.drop_index("ix_subscription_events_subscription_id", table_name="subscription_events")
    op.drop_index("ix_subscription_events_user_id", table_name="subscription_events")
    op.drop_table("subscription_events")
    op.drop_index("ix_subscriptions_plan_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_subscription_account_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_subscription_accounts_user_id", table_name="subscription_accounts")
    op.drop_table("subscription_accounts")
    op.drop_index("ix_subscription_plans_tier", table_name="subscription_plans")
    op.drop_table("subscription_plans")
