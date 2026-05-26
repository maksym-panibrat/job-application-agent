"""add jobs content hash

Revision ID: d8f2c4a9b1e7
Revises: 5a6b7c8d9e0f
Create Date: 2026-05-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8f2c4a9b1e7"
down_revision: str | None = "5a6b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("content_hash", sa.String(), nullable=True))
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_column("jobs", "content_hash")
