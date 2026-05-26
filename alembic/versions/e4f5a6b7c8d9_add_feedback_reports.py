"""add feedback reports

Revision ID: e4f5a6b7c8d9
Revises: 05b608a37f60, 5a6b7c8d9e0f
Create Date: 2026-05-25 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: tuple[str, str] = ("05b608a37f60", "5a6b7c8d9e0f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=False),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "notification_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "notification_error",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_reports_user_id"), "feedback_reports", ["user_id"])
    op.create_index(op.f("ix_feedback_reports_user_email"), "feedback_reports", ["user_email"])
    op.create_index(op.f("ix_feedback_reports_category"), "feedback_reports", ["category"])
    op.create_index(
        op.f("ix_feedback_reports_notification_status"),
        "feedback_reports",
        ["notification_status"],
    )
    op.create_index(op.f("ix_feedback_reports_created_at"), "feedback_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_reports_created_at"), table_name="feedback_reports")
    op.drop_index(
        op.f("ix_feedback_reports_notification_status"),
        table_name="feedback_reports",
    )
    op.drop_index(op.f("ix_feedback_reports_category"), table_name="feedback_reports")
    op.drop_index(op.f("ix_feedback_reports_user_email"), table_name="feedback_reports")
    op.drop_index(op.f("ix_feedback_reports_user_id"), table_name="feedback_reports")
    op.drop_table("feedback_reports")
