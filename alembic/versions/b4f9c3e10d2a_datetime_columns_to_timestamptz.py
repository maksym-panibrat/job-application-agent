"""datetime columns to timestamptz

Revision ID: b4f9c3e10d2a
Revises: 25a70e8d5968
Create Date: 2026-04-15

Convert all TIMESTAMP WITHOUT TIME ZONE columns to TIMESTAMP WITH TIME ZONE.
Postgres treats existing naive values as UTC on ALTER — no data transform needed.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4f9c3e10d2a"
down_revision: Union[str, None] = "25a70e8d5968"
branch_labels = None
depends_on = None

_TIMESTAMPTZ = sa.DateTime(timezone=True)
_TIMESTAMP = sa.DateTime(timezone=False)

_COLUMNS = [
    # (table, column)
    ("jobs", "posted_at"),
    ("jobs", "fetched_at"),
    ("applications", "created_at"),
    ("applications", "updated_at"),
    ("generated_documents", "created_at"),
    ("user_profiles", "search_expires_at"),
    ("user_profiles", "created_at"),
    ("user_profiles", "updated_at"),
    ("work_experiences", "start_date"),
    ("work_experiences", "end_date"),
    ("job_search_cache", "fetched_at"),
    ("job_search_cache", "expires_at"),
]


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(table, column, type_=_TIMESTAMPTZ, existing_type=_TIMESTAMP)


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.alter_column(table, column, type_=_TIMESTAMP, existing_type=_TIMESTAMPTZ)
