"""Integration test for the d7e2b40a5301 re-clean data migration.

Drives the migration's `reclean_jobs_description` helper against the
testcontainer DB to confirm it rewrites broken descriptions (decoded
HTML) into markdown and leaves rows with no source content alone.
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
    / "d7e2b40a5301_reclean_jobs_description.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("reclean_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sync_engine(postgres_container, db_session):
    # `db_session` (async) provisions the schema we need on the shared
    # testcontainer; we just want a sync handle to it for the migration loop.
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
async def test_reclean_rewrites_decoded_html_to_markdown(sync_engine):
    """The prod symptom (2026-05-10): description_raw is entity-encoded HTML,
    description is the same HTML with entities decoded but no markdown
    conversion. After the fixed cleaner runs over the raw payload, description
    should be proper markdown."""
    mig = _load_migration()

    encoded_raw = (
        "&lt;h2&gt;Role&lt;/h2&gt;\n"
        "&lt;p&gt;Build &lt;strong&gt;Python&lt;/strong&gt; things.&lt;/p&gt;"
    )
    pre_migration_bad_description = "<h2>Role</h2>\n<p>Build <strong>Python</strong> things.</p>"

    with sync_engine.begin() as conn:
        broken_id = _insert_job(
            conn,
            description=pre_migration_bad_description,
            description_raw=encoded_raw,
        )
        null_id = _insert_job(
            conn,
            description=None,
            description_raw=encoded_raw,
        )
        # No-op rows: nothing to compute from.
        empty_id = _insert_job(conn, description=None, description_raw=None)
        whitespace_id = _insert_job(conn, description=None, description_raw="   \n  ")

    with sync_engine.begin() as conn:
        updated = mig.reclean_jobs_description(conn)

    assert updated == 2  # broken_id + null_id

    with sync_engine.connect() as conn:
        rows = {
            row.id: row.description
            for row in conn.execute(
                sa.text("SELECT id, description FROM jobs WHERE id IN (:a, :b, :c, :d)"),
                {"a": broken_id, "b": null_id, "c": empty_id, "d": whitespace_id},
            ).fetchall()
        }

    for fixed_id in (broken_id, null_id):
        out = rows[fixed_id]
        assert out is not None
        assert "## Role" in out
        assert "**Python**" in out
        assert "<h2>" not in out
        assert "<strong>" not in out
        assert "&lt;" not in out

    assert rows[empty_id] is None
    assert rows[whitespace_id] is None


@pytest.mark.asyncio
async def test_reclean_is_idempotent(sync_engine):
    mig = _load_migration()

    encoded = "&lt;p&gt;One time&lt;/p&gt;"
    with sync_engine.begin() as conn:
        _insert_job(conn, description=None, description_raw=encoded)

    with sync_engine.begin() as conn:
        first = mig.reclean_jobs_description(conn)
    with sync_engine.begin() as conn:
        second = mig.reclean_jobs_description(conn)

    assert first == 1
    assert second == 0  # already up to date — nothing to rewrite


@pytest.mark.asyncio
async def test_reclean_leaves_already_clean_rows_alone(sync_engine):
    """Rows where description already equals clean_html_to_markdown(description_raw)
    must not be needlessly UPDATEd — the loop's count reflects that."""
    mig = _load_migration()

    encoded_raw = "&lt;p&gt;Already clean&lt;/p&gt;"
    # Hand-compute the expected markdown so we don't depend on the cleaner here.
    from app.services.html_cleaner import clean_html_to_markdown

    expected_md = clean_html_to_markdown(encoded_raw)

    with sync_engine.begin() as conn:
        _insert_job(conn, description=expected_md, description_raw=encoded_raw)

    with sync_engine.begin() as conn:
        updated = mig.reclean_jobs_description(conn)

    assert updated == 0
