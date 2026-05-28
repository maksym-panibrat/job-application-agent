import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.llm_match_batch import LLMMatchBatch, LLMMatchBatchItem


@pytest.mark.asyncio
async def test_llm_match_batches_table_exists(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'llm_match_batches'
                ORDER BY ordinal_position
                """
            )
        )
    ).all()
    cols = {row[0]: (row[1], row[2]) for row in rows}
    expected = {
        "id": ("uuid", "NO"),
        "profile_id": ("uuid", "NO"),
        "provider": ("text", "NO"),
        "provider_batch_id": ("text", "YES"),
        "model": ("text", "NO"),
        "prompt_version": ("text", "NO"),
        "status": ("text", "NO"),
        "submitted_at": ("timestamp with time zone", "YES"),
        "completed_at": ("timestamp with time zone", "YES"),
        "next_poll_at": ("timestamp with time zone", "YES"),
        "last_polled_at": ("timestamp with time zone", "YES"),
        "last_error": ("text", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    }
    for column, expected_shape in expected.items():
        assert cols[column] == expected_shape


@pytest.mark.asyncio
async def test_llm_match_batch_items_table_exists(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'llm_match_batch_items'
                ORDER BY ordinal_position
                """
            )
        )
    ).all()
    cols = {row[0]: (row[1], row[2]) for row in rows}
    expected = {
        "id": ("uuid", "NO"),
        "batch_id": ("uuid", "NO"),
        "application_id": ("uuid", "NO"),
        "provider_request_key": ("text", "NO"),
        "request_hash": ("text", "NO"),
        "status": ("text", "NO"),
        "score": ("double precision", "YES"),
        "summary": ("text", "YES"),
        "rationale": ("text", "YES"),
        "strengths": ("ARRAY", "NO"),
        "gaps": ("ARRAY", "NO"),
        "error": ("text", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    }
    for column, expected_shape in expected.items():
        assert cols[column] == expected_shape


@pytest.mark.asyncio
async def test_llm_match_batch_indexes_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename IN ('llm_match_batches', 'llm_match_batch_items')
                """
            )
        )
    ).all()
    by_name = {row[0]: row[1] for row in rows}
    assert "uq_llm_match_batches_one_active_per_profile" in by_name
    assert "ix_llm_match_batches_next_poll_at" in by_name
    assert "uq_llm_match_batch_items_active_attempt" in by_name
    assert "ix_llm_match_batch_items_batch_status" in by_name

    batch_unique_index = by_name["uq_llm_match_batches_one_active_per_profile"].upper()
    assert "CREATE UNIQUE INDEX" in batch_unique_index
    assert "(PROFILE_ID)" in batch_unique_index
    assert "WHERE" in batch_unique_index
    assert "'BUILDING'" in batch_unique_index
    assert "'SUBMITTED'" in batch_unique_index
    assert "'IMPORTING'" in batch_unique_index

    item_unique_index = by_name["uq_llm_match_batch_items_active_attempt"].upper()
    assert "CREATE UNIQUE INDEX" in item_unique_index
    assert "(APPLICATION_ID, REQUEST_HASH)" in item_unique_index
    assert "WHERE" in item_unique_index
    assert "STATUS = 'SUBMITTED'" in item_unique_index


def test_llm_match_batch_migration_file_matches_schema_contract():
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "b7c8d9e0f1a2_add_llm_match_batches.py"
    )
    spec = importlib.util.spec_from_file_location(
        "b7c8d9e0f1a2_add_llm_match_batches",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "b7c8d9e0f1a2"
    assert migration.down_revision == "e4f5a6b7c8d9"

    source = migration_path.read_text()
    assert "llm_match_batches" in source
    assert "llm_match_batch_items" in source
    assert "uq_llm_match_batches_one_active_per_profile" in source
    assert "ix_llm_match_batches_next_poll_at" in source
    assert "uq_llm_match_batch_items_active_attempt" in source
    assert "ix_llm_match_batch_items_batch_status" in source
    assert 'server_default=sa.text("now()")' in source
    assert 'server_default=sa.text("\'{}\'::text[]")' in source


def test_llm_match_batch_model_timestamp_defaults_match_migration():
    for table in (LLMMatchBatch.__table__, LLMMatchBatchItem.__table__):
        for column_name in ("created_at", "updated_at"):
            default = table.c[column_name].server_default
            assert default is not None
            assert str(default.arg) == "now()"
