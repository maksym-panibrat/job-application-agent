"""add salary and contract_type to jobs

Revision ID: 25a70e8d5968
Revises: cfd76b2ed274
Create Date: 2026-04-15 14:48:37.492820

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "25a70e8d5968"
down_revision: Union[str, None] = "cfd76b2ed274"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("salary", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column(
        "jobs", sa.Column("contract_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "contract_type")
    op.drop_column("jobs", "salary")
