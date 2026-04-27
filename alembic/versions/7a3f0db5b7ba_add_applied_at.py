"""add_applied_at

Revision ID: 7a3f0db5b7ba
Revises: 8bdb4dedbd38
Create Date: 2026-04-26 20:33:01.718834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '7a3f0db5b7ba'
down_revision: Union[str, None] = '8bdb4dedbd38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "applied_at")
