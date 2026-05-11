"""re-clean jobs.description with the fixed html_cleaner

Existing rows have decoded-HTML stored in description because the pre-fix
clean_html_to_markdown ran BeautifulSoup on entity-encoded payloads from
Greenhouse's boards-api — BeautifulSoup parsed `&lt;h2&gt;` as text,
markdownify had no tags to convert, and decoded HTML ended up in the
column. Symptom on 2026-05-10: literal <p>/<h2>/<strong> rendered as
visible characters on the match-detail page.

The cleaner is now fixed (html.unescape() before BeautifulSoup). This
migration re-runs it against every row's description_raw and overwrites
description with the result. Rows whose description_raw is empty/whitespace
or whose cleaner output is empty are left alone (no point storing "").

Idempotent — running again produces the same output. Safe to overwrite
existing description because the column is system-managed (no user edits).

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


def reclean_jobs_description(connection: Connection) -> int:
    """Recompute description = clean_html_to_markdown(description_raw) for
    every row that has non-empty description_raw. Returns number of rows
    where description was actually updated (cleaned output differed from
    what was stored).

    Extracted so the integration test can run it directly against a sync
    engine bound to the testcontainer without invoking the full Alembic
    pipeline.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT id, description_raw, description
            FROM jobs
            WHERE description_raw IS NOT NULL
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
        if md == row.description:
            continue
        connection.execute(update_stmt, {"md": md, "id": row.id})
        updated += 1
    return updated


def upgrade() -> None:
    reclean_jobs_description(op.get_bind())


def downgrade() -> None:
    # Irreversible — we no longer have the pre-migration description values.
    # A no-op downgrade is safer than guessing.
    pass
