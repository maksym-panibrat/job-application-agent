"""add_application_match_summary

Revision ID: 4a27b82fcbb9
Revises: add76003700d
Create Date: 2026-04-29 20:51:44.184328

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a27b82fcbb9"
down_revision: str | None = "add76003700d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("match_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "match_summary")
