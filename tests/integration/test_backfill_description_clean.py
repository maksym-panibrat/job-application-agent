"""Backfill script populates description_clean for rows where it's NULL."""

import pytest
from scripts.backfill_job_description_clean import run_backfill
from sqlmodel import select

from app.models.job import Job


@pytest.mark.asyncio
async def test_backfill_populates_null_rows(db_session):
    # Insert rows with description_md but NULL description_clean (simulating legacy state)
    j1 = Job(
        source="greenhouse_board",
        external_id="bf-1",
        title="t1",
        company_name="c1",
        apply_url="https://x/1",
        description_md="<p>hello <strong>one</strong></p>",
        description_clean=None,
    )
    j2 = Job(
        source="greenhouse_board",
        external_id="bf-2",
        title="t2",
        company_name="c2",
        apply_url="https://x/2",
        description_md="<p>hello two</p>",
        description_clean="already-set",  # should NOT be touched
    )
    j3 = Job(
        source="greenhouse_board",
        external_id="bf-3",
        title="t3",
        company_name="c3",
        apply_url="https://x/3",
        description_md=None,
        description_clean=None,  # NULL desc_md → backfill should set '' or skip
    )
    db_session.add_all([j1, j2, j3])
    await db_session.commit()

    processed, skipped = await run_backfill(batch_size=10, session=db_session)

    # Re-read
    result = await db_session.execute(
        select(Job).where(Job.external_id.in_(["bf-1", "bf-2", "bf-3"]))
    )
    by_id = {j.external_id: j for j in result.scalars().all()}

    assert "**one**" in (by_id["bf-1"].description_clean or "")
    assert by_id["bf-2"].description_clean == "already-set"  # untouched
    # bf-3: description_md is NULL — backfill writes '' so NOT NULL anymore
    assert by_id["bf-3"].description_clean == ""
    assert processed >= 2
