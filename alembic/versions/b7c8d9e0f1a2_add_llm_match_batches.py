"""add llm match batches

Revision ID: b7c8d9e0f1a2
Revises: e4f5a6b7c8d9
Create Date: 2026-05-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_match_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_batch_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_llm_match_batches_one_active_per_profile",
        "llm_match_batches",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('building', 'submitted', 'importing')"),
    )
    op.create_index(
        "ix_llm_match_batches_next_poll_at",
        "llm_match_batches",
        ["next_poll_at"],
    )

    op.create_table(
        "llm_match_batch_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("provider_request_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "strengths",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "gaps",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["llm_match_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_llm_match_batch_items_active_attempt",
        "llm_match_batch_items",
        ["application_id", "request_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'submitted'"),
    )
    op.create_index(
        "ix_llm_match_batch_items_batch_status",
        "llm_match_batch_items",
        ["batch_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_match_batch_items_batch_status",
        table_name="llm_match_batch_items",
    )
    op.drop_index(
        "uq_llm_match_batch_items_active_attempt",
        table_name="llm_match_batch_items",
    )
    op.drop_table("llm_match_batch_items")
    op.drop_index(
        "ix_llm_match_batches_next_poll_at",
        table_name="llm_match_batches",
    )
    op.drop_index(
        "uq_llm_match_batches_one_active_per_profile",
        table_name="llm_match_batches",
    )
    op.drop_table("llm_match_batches")
