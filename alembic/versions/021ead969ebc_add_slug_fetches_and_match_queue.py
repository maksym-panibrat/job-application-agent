"""add slug_fetches and match queue

Revision ID: 021ead969ebc
Revises: 9c4e8a2bd6f1
Create Date: 2026-04-28 21:15:38.424602

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "021ead969ebc"
down_revision: str | None = "9c4e8a2bd6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New table: slug_fetches
    op.create_table(
        "slug_fetches",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("consecutive_404_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_5xx_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_invalid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("invalid_reason", sa.String(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("source", "slug", name="pk_slug_fetches"),
    )
    op.create_index(
        "ix_slug_fetches_queued",
        "slug_fetches",
        ["queued_at", "claimed_at"],
    )

    # Application: match queue columns
    op.add_column(
        "applications",
        sa.Column("match_status", sa.String(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("match_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "applications",
        sa.Column("match_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("match_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: any application that already has a score is matched; rest are pending_match
    op.execute(
        "UPDATE applications SET match_status = "
        "CASE WHEN match_score IS NOT NULL THEN 'matched' ELSE 'pending_match' END"
    )
    op.alter_column(
        "applications",
        "match_status",
        nullable=False,
        server_default="pending_match",
    )
    op.create_index(
        "ix_applications_match_queue",
        "applications",
        ["match_status", "match_queued_at"],
    )

    # UserProfile: sync visibility
    op.add_column(
        "user_profiles",
        sa.Column("last_sync_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "last_sync_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Backfill slug_fetches: every distinct slug across active profiles seeded
    # NULL last_fetched_at so the next cron treats them all as new (fetch
    # immediately).
    op.execute(
        """
        INSERT INTO slug_fetches (source, slug)
        SELECT DISTINCT 'greenhouse_board', jsonb_array_elements_text(
            COALESCE(target_company_slugs->'greenhouse', '[]'::jsonb)
        )
        FROM user_profiles
        WHERE search_active = true
        ON CONFLICT (source, slug) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_applications_match_queue", table_name="applications")
    op.drop_column("applications", "match_claimed_at")
    op.drop_column("applications", "match_queued_at")
    op.drop_column("applications", "match_attempts")
    op.drop_column("applications", "match_status")
    op.drop_column("user_profiles", "last_sync_summary")
    op.drop_column("user_profiles", "last_sync_completed_at")
    op.drop_column("user_profiles", "last_sync_requested_at")
    op.drop_index("ix_slug_fetches_queued", table_name="slug_fetches")
    op.drop_table("slug_fetches")
