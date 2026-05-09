"""add companies.is_curated

Revision ID: 8fa8c82211c3
Revises: bf8093d778c9
Create Date: 2026-05-08 20:37:57.650128

"""

import sqlalchemy as sa

from alembic import op

revision = "8fa8c82211c3"
down_revision = "bf8093d778c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "is_curated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_companies_is_curated", "companies", ["is_curated"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_companies_is_curated", table_name="companies")
    op.drop_column("companies", "is_curated")
