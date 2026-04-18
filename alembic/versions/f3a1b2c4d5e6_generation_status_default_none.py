"""generation_status default: pending -> none

Revision ID: f3a1b2c4d5e6
Revises: b4f9c3e10d2a
Create Date: 2026-04-16

Generation now only starts on explicit user approval (Approve button).
Applications that were sitting in 'pending' without being picked up by the
scheduler (i.e. generation_attempts == 0) are reset to 'none'.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a1b2c4d5e6'
down_revision: Union[str, None] = 'b4f9c3e10d2a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reset unstarted pending rows to 'none'
    op.execute(
        "UPDATE applications SET generation_status = 'none' "
        "WHERE generation_status = 'pending' AND generation_attempts = 0"
    )
    # Change the column default
    op.alter_column(
        'applications',
        'generation_status',
        server_default='none',
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'applications',
        'generation_status',
        server_default='pending',
        existing_type=sa.String(),
        existing_nullable=False,
    )
    op.execute(
        "UPDATE applications SET generation_status = 'pending' "
        "WHERE generation_status = 'none'"
    )
