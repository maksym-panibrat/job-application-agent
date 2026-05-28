import pytest
from sqlalchemy import text


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
