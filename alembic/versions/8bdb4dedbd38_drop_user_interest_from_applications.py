"""drop user_interest from applications

Revision ID: 8bdb4dedbd38
Revises: 84325e37e095
Create Date: 2026-04-22

"""

import sqlalchemy as sa

from alembic import op

revision: str = "8bdb4dedbd38"
down_revision: str | None = "84325e37e095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("applications", "user_interest")


def downgrade() -> None:
    op.add_column("applications", sa.Column("user_interest", sa.String(), nullable=True))
