"""phase b migration 1 — work_queue table, indexes, new application columns, backfill

T11a: work_queue table + indexes.
T11b: cover_letter_content + generated_at columns on applications.
T11c: data backfill from legacy queue state (slug_fetches + applications).

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


BACKFILL_FETCH_SLUG_SQL = """
INSERT INTO work_queue (job_type, payload, status, enqueued_at, dedupe_key)
SELECT 'fetch-slug',
       jsonb_build_object('provider', source, 'slug', slug),
       'pending',
       COALESCE(queued_at, now()),
       'fetch-slug:' || source || ':' || slug
FROM slug_fetches
WHERE queued_at IS NOT NULL
  AND is_invalid = false
  AND (claimed_at IS NULL OR claimed_at < now() - interval '300 seconds')
ON CONFLICT (job_type, dedupe_key)
    WHERE status IN ('pending','in_progress') AND dedupe_key IS NOT NULL
DO NOTHING;
"""

BACKFILL_MATCH_SQL = """
INSERT INTO work_queue (job_type, payload, status, enqueued_at, attempts, dedupe_key)
SELECT 'match',
       jsonb_build_object('application_id', id::text),
       'pending',
       COALESCE(match_queued_at, now()),
       COALESCE(match_attempts, 0),
       'match:' || id::text
FROM applications
WHERE match_status = 'pending_match'
  AND (match_claimed_at IS NULL OR match_claimed_at < now() - interval '360 seconds')
ON CONFLICT (job_type, dedupe_key)
    WHERE status IN ('pending','in_progress') AND dedupe_key IS NOT NULL
DO NOTHING;
"""

BACKFILL_GENERATION_SQL = """
INSERT INTO work_queue (job_type, payload, status, enqueued_at, attempts, dedupe_key)
SELECT 'generate-cover-letter',
       jsonb_build_object('application_id', id::text),
       'pending',
       now(),
       COALESCE(generation_attempts, 0),
       'generate-cover-letter:' || id::text
FROM applications
WHERE generation_status = 'pending'
ON CONFLICT (job_type, dedupe_key)
    WHERE status IN ('pending','in_progress') AND dedupe_key IS NOT NULL
DO NOTHING;
"""


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
    # T11b additions:
    op.add_column(
        "applications", sa.Column("cover_letter_content", sa.Text, nullable=True)
    )
    op.add_column(
        "applications",
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # T11c backfill — preserves legacy queue state per spec.
    # SQL bodies are module-level constants so the test harness imports the
    # exact same strings (Codex round-2 plan finding #4).
    op.execute(BACKFILL_FETCH_SLUG_SQL)
    op.execute(BACKFILL_MATCH_SQL)
    op.execute(BACKFILL_GENERATION_SQL)


def downgrade() -> None:
    # T11b reverse:
    op.drop_column("applications", "generated_at")
    op.drop_column("applications", "cover_letter_content")
    # T11a downgrade reverses table + indexes only.
    # T11c will extend this downgrade() at the START with its own reverse ops.
    op.drop_index("work_queue_in_progress_claimed", table_name="work_queue")
    op.drop_index("work_queue_pending", table_name="work_queue")
    op.drop_index("work_queue_dedupe", table_name="work_queue")
    op.drop_table("work_queue")
