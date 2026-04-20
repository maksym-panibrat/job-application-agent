"""add user_interest to applications

Revision ID: a8c7115b85f5
Revises: ac99d59f1079
Create Date: 2026-04-19 21:57:15.391403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'a8c7115b85f5'
down_revision: Union[str, None] = 'ac99d59f1079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('applications', sa.Column('user_interest', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('applications', 'user_interest')
