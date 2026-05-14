"""phase b migration 2 — drop legacy queue columns

Revision ID: 5a6b7c8d9e0f
Revises: 97c970fc78e2
Create Date: 2026-05-14 05:25:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a6b7c8d9e0f"
down_revision: str | None = "97c970fc78e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("slug_fetches", "queued_at")
    op.drop_column("slug_fetches", "claimed_at")
    op.drop_column("slug_fetches", "last_status")
    op.drop_column("applications", "match_status")
    op.drop_column("applications", "match_queued_at")
    op.drop_column("applications", "match_claimed_at")
    op.drop_column("applications", "match_attempts")


def downgrade() -> None:
    # Recreate schema-compatible empty legacy queue columns for emergency Path C.
    # Data is not restored; old images can SELECT these columns after downgrade,
    # but queue ownership is intentionally rebuilt by the legacy schedulers.
    op.add_column("applications", sa.Column("match_attempts", sa.Integer, nullable=True))
    op.add_column(
        "applications",
        sa.Column("match_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("match_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("applications", sa.Column("match_status", sa.Text, nullable=True))
    op.add_column("slug_fetches", sa.Column("last_status", sa.Text, nullable=True))
    op.add_column(
        "slug_fetches",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "slug_fetches",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
