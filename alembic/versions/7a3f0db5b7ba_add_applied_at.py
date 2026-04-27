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
    # Add applied_at — manual transition timestamp.
    op.add_column(
        "applications",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Drop the columns the SQLModel no longer maps (submit pipeline removed).
    # PR 5 was originally going to bundle these; we drop them here to avoid
    # NOT NULL violations on inserts of new Job/Application rows that don't
    # specify them.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("ats_type")
        batch.drop_column("supports_api_apply")
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("submitted_at")
        batch.drop_column("submission_method")
        batch.drop_column("submission_result")


def downgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("submission_result", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("submission_method", sa.String(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "supports_api_apply",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("jobs", sa.Column("ats_type", sa.String(), nullable=True))
    op.drop_column("applications", "applied_at")
