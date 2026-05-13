import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_work_queue_table_exists(db_session):
    rows = (
        await db_session.execute(
            text(
                """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name='work_queue'
        ORDER BY ordinal_position
    """
            )
        )
    ).all()
    cols = {r[0]: (r[1], r[2]) for r in rows}
    expected = {
        "id": ("bigint", "NO"),
        "job_type": ("text", "NO"),
        "payload": ("jsonb", "NO"),
        "status": ("text", "NO"),
        "enqueued_at": ("timestamp with time zone", "NO"),
        "claimed_at": ("timestamp with time zone", "YES"),
        "claimed_by": ("text", "YES"),
        "not_before": ("timestamp with time zone", "YES"),
        "completed_at": ("timestamp with time zone", "YES"),
        "attempts": ("integer", "NO"),
        "last_error": ("text", "YES"),
        "dedupe_key": ("text", "YES"),
    }
    for col, (dtype, nullable) in expected.items():
        assert col in cols, f"missing column: {col}"
        assert cols[col][0] == dtype, f"{col}: got {cols[col][0]}, want {dtype}"
        assert cols[col][1] == nullable, (
            f"{col} nullable: got {cols[col][1]}, want {nullable}"
        )


@pytest.mark.asyncio
async def test_work_queue_indexes(db_session):
    rows = (
        await db_session.execute(
            text(
                """
        SELECT indexname, indexdef FROM pg_indexes WHERE tablename='work_queue'
    """
            )
        )
    ).all()
    names = {r[0] for r in rows}
    assert "work_queue_dedupe" in names
    assert "work_queue_pending" in names
    assert "work_queue_in_progress_claimed" in names
    by_name = {r[0]: r[1].upper() for r in rows}
    assert "UNIQUE" in by_name["work_queue_dedupe"]
    assert "PENDING" in by_name["work_queue_pending"]


@pytest.mark.asyncio
async def test_applications_new_columns(db_session):
    """T11b adds cover_letter_content + generated_at."""
    rows = (
        await db_session.execute(
            text(
                """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name='applications'
          AND column_name IN ('cover_letter_content', 'generated_at')
    """
            )
        )
    ).all()
    cols = {r[0]: (r[1], r[2]) for r in rows}
    assert cols["cover_letter_content"] == ("text", "YES")
    assert cols["generated_at"] == ("timestamp with time zone", "YES")
