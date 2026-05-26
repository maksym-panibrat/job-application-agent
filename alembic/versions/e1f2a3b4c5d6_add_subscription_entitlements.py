"""add subscription entitlements

Revision ID: e1f2a3b4c5d6
Revises: d8f2c4a9b1e7
Create Date: 2026-05-25

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d8f2c4a9b1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL_ACTIVE_PROFILE_TRIAL_SQL = """
UPDATE user_profiles
SET search_expires_at = NOW() + interval '7 days'
WHERE search_active IS TRUE
  AND search_expires_at IS NULL
"""


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "subscription_plan",
            sa.String(),
            nullable=False,
            server_default=sa.text("'free'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "subscription_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'inactive'"),
        ),
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
    op.execute(BACKFILL_ACTIVE_PROFILE_TRIAL_SQL)


def downgrade() -> None:
    op.drop_constraint("ck_users_subscription_status", "users", type_="check")
    op.drop_constraint("ck_users_subscription_plan", "users", type_="check")
    op.drop_column("users", "subscription_current_period_end")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "subscription_plan")
