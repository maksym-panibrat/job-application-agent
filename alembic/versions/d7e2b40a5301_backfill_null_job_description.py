"""backfill NULL jobs.description from description_raw

Some rows still have description IS NULL — ingested before the original
description_clean column was added (add76003700d, 2026-04-29) and never
hit by the standalone backfill script that lived briefly at
scripts/backfill_job_description_clean.py (added in 50ea88e, deleted in
40f9b19 during the provider-agnostic refactor). The rename migration
bf8093d778c9 carried the NULLs forward as `description=NULL`.

In the UI those rows hit the `description ?? description_raw` fallback in
ApplicationReview.tsx and render as literal HTML tags in <pre>, which is
the prod symptom this migration exists to clear.

Idempotent — re-running converts no rows. Skips rows where description_raw
is empty/whitespace-only.

Revision ID: d7e2b40a5301
Revises: c2f4610cf147
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from app.services.html_cleaner import clean_html_to_markdown

revision: str = "d7e2b40a5301"
down_revision: str | None = "c2f4610cf147"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def backfill_jobs_description(connection: Connection) -> int:
    """Populate jobs.description for rows that have NULL description but
    non-empty description_raw. Returns number of rows updated.

    Extracted so the integration test can run it directly against a sync
    engine bound to the testcontainer without invoking the full Alembic
    pipeline.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT id, description_raw
            FROM jobs
            WHERE description IS NULL
              AND description_raw IS NOT NULL
              AND length(btrim(description_raw)) > 0
            """
        )
    ).fetchall()
    if not rows:
        return 0

    update_stmt = sa.text("UPDATE jobs SET description = :md WHERE id = :id")
    updated = 0
    for row in rows:
        md = clean_html_to_markdown(row.description_raw)
        if not md:
            continue
        connection.execute(update_stmt, {"md": md, "id": row.id})
        updated += 1
    return updated


def upgrade() -> None:
    backfill_jobs_description(op.get_bind())


def downgrade() -> None:
    # Irreversible — we don't know which rows we filled because the original
    # NULLs are gone. A no-op downgrade is safer than guessing and clearing
    # legitimately-populated rows.
    pass
