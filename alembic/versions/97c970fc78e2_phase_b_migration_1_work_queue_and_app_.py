"""phase b migration 1 — work_queue table, indexes, backfill, new application columns

T11a: work_queue table + indexes (this commit).
T11b: cover_letter_content + generated_at columns on applications (extends upgrade()).
T11c: data backfill from legacy queue state (extends upgrade()).

Revision ID: 97c970fc78e2
Revises: d7e2b40a5301
Create Date: 2026-05-12 14:08:33.615661

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '97c970fc78e2'
down_revision: Union[str, None] = 'd7e2b40a5301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # T11a: table + indexes only. T11b extends upgrade() with applications columns.
    # T11c extends upgrade() with the data backfill.
    op.create_table(
        "work_queue",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.Text, nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.Text, nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("dedupe_key", sa.Text, nullable=True),
    )
    op.create_index(
        "work_queue_dedupe",
        "work_queue",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'in_progress') AND dedupe_key IS NOT NULL"
        ),
    )
    op.create_index(
        "work_queue_pending",
        "work_queue",
        ["job_type", "enqueued_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "work_queue_in_progress_claimed",
        "work_queue",
        ["claimed_at"],
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    # T11a downgrade reverses table + indexes only.
    # T11b and T11c will extend this downgrade() at the START with their own reverse ops.
    op.drop_index("work_queue_in_progress_claimed", table_name="work_queue")
    op.drop_index("work_queue_pending", table_name="work_queue")
    op.drop_index("work_queue_dedupe", table_name="work_queue")
    op.drop_table("work_queue")
