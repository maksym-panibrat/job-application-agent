"""Integration test for the d7e2b40a5301 backfill data migration.

Drives the migration's `backfill_jobs_description` helper against the
testcontainer DB to confirm it populates exactly the rows that should be
populated and leaves the rest alone.
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "alembic"
    / "versions"
    / "d7e2b40a5301_backfill_null_job_description.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("backfill_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sync_engine(postgres_container, db_session):
    # `db_session` (async) provisions the schema we need on the shared
    # testcontainer; we just want a sync handle to it for the backfill.
    raw = postgres_container.get_connection_url()
    url = raw.replace("+psycopg2", "+psycopg")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _insert_job(
    conn: sa.engine.Connection,
    *,
    description: str | None,
    description_raw: str | None,
) -> uuid.UUID:
    job_id = uuid.uuid4()
    conn.execute(
        sa.text(
            """
            INSERT INTO jobs (
                id, source, external_id, title, company_name,
                description, description_raw,
                apply_url, fetched_at, is_active
            ) VALUES (
                :id, 'greenhouse', :ext, 'T', 'Co',
                :description, :description_raw,
                'https://x', :fetched_at, true
            )
            """
        ),
        {
            "id": job_id,
            "ext": str(job_id),
            "description": description,
            "description_raw": description_raw,
            "fetched_at": datetime.now(UTC),
        },
    )
    return job_id


@pytest.mark.asyncio
async def test_backfill_populates_html_only_when_description_is_null(sync_engine):
    mig = _load_migration()

    with sync_engine.begin() as conn:
        # Row that needs backfilling: HTML in raw, NULL description.
        html_id = _insert_job(
            conn,
            description=None,
            description_raw="<h2>Role</h2><p>Build <strong>Python</strong> things.</p>",
        )
        # Row that must NOT be touched: existing markdown.
        markdown_id = _insert_job(
            conn,
            description="# Existing markdown",
            description_raw="<p>raw is stale</p>",
        )
        # Row that can't be backfilled: both columns empty.
        empty_id = _insert_job(conn, description=None, description_raw=None)
        # Row that can't be backfilled: whitespace-only raw.
        whitespace_id = _insert_job(conn, description=None, description_raw="   \n  ")

    with sync_engine.begin() as conn:
        updated = mig.backfill_jobs_description(conn)

    assert updated == 1

    with sync_engine.connect() as conn:
        rows = {
            row.id: row.description
            for row in conn.execute(
                sa.text("SELECT id, description FROM jobs WHERE id IN (:a, :b, :c, :d)"),
                {"a": html_id, "b": markdown_id, "c": empty_id, "d": whitespace_id},
            ).fetchall()
        }

    assert rows[html_id] is not None
    assert "Role" in rows[html_id]
    assert "Python" in rows[html_id]
    assert "<h2>" not in rows[html_id]
    assert "<strong>" not in rows[html_id]
    # Markdown row left alone.
    assert rows[markdown_id] == "# Existing markdown"
    # No-content rows stay NULL.
    assert rows[empty_id] is None
    assert rows[whitespace_id] is None


@pytest.mark.asyncio
async def test_backfill_is_idempotent(sync_engine):
    mig = _load_migration()

    with sync_engine.begin() as conn:
        _insert_job(
            conn,
            description=None,
            description_raw="<p>One time</p>",
        )

    with sync_engine.begin() as conn:
        first = mig.backfill_jobs_description(conn)
    with sync_engine.begin() as conn:
        second = mig.backfill_jobs_description(conn)

    assert first >= 1
    assert second == 0
