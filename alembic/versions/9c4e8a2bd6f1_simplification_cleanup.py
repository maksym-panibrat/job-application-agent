"""simplification_cleanup

Drops 5 unused profile fields (work_authorization, requires_sponsorship,
salary_expectation_usd, available_from, standard_answers) and the now-orphaned
job_search_cache table left behind after PR 2 deleted the source modules that
populated it.

Revision ID: 9c4e8a2bd6f1
Revises: 7a3f0db5b7ba
Create Date: 2026-04-27 12:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "9c4e8a2bd6f1"
down_revision: Union[str, None] = "7a3f0db5b7ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("user_profiles", "standard_answers")
    op.drop_column("user_profiles", "available_from")
    op.drop_column("user_profiles", "salary_expectation_usd")
    op.drop_column("user_profiles", "requires_sponsorship")
    op.drop_column("user_profiles", "work_authorization")

    op.execute("DROP TABLE IF EXISTS job_search_cache")


def downgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("work_authorization", sa.String(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("requires_sponsorship", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("salary_expectation_usd", sa.Integer(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("available_from", sa.String(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "standard_answers",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "job_search_cache",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("query_hash", sa.String(), nullable=False, unique=True),
        sa.Column("query", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("results", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
