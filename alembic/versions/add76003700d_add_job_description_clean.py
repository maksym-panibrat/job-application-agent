"""add_job_description_clean

Revision ID: add76003700d
Revises: 021ead969ebc
Create Date: 2026-04-29 20:19:45.884202

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add76003700d'
down_revision: str | None = '021ead969ebc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("description_clean", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "description_clean")
