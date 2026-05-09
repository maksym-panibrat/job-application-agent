"""add companies.tags

Revision ID: c2f4610cf147
Revises: 8fa8c82211c3
Create Date: 2026-05-08 23:47:07.997242

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "c2f4610cf147"
down_revision = "8fa8c82211c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "tags",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "tags")
