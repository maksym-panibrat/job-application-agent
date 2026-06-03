import uuid

import pytest
from sqlmodel import select

from app.contracts.workflow import JobType
from app.models.work_queue import WorkQueue
from app.worker.enqueue_intents import (
    enqueue_batch_match,
    enqueue_cover_letter,
    enqueue_fetch_slug,
    enqueue_match,
)


@pytest.mark.asyncio
async def test_enqueue_intents_write_canonical_job_types_payloads_and_keys(db_session):
    aid = uuid.uuid4()
    pid = uuid.uuid4()

    await enqueue_fetch_slug(db_session, provider="greenhouse", slug="openai")
    await enqueue_match(db_session, aid)
    await enqueue_batch_match(db_session, pid, max_items=7)
    await enqueue_cover_letter(db_session, aid)
    await db_session.commit()

    rows = (await db_session.execute(select(WorkQueue))).scalars().all()
    by_type = {row.job_type: row for row in rows}

    assert by_type[JobType.FETCH_SLUG].payload == {
        "provider": "greenhouse",
        "slug": "openai",
    }
    assert by_type[JobType.FETCH_SLUG].dedupe_key == "fetch-slug:greenhouse:openai"

    assert by_type[JobType.MATCH].payload == {"application_id": str(aid)}
    assert by_type[JobType.MATCH].dedupe_key == f"match:{aid}"

    assert by_type[JobType.BATCH_MATCH].payload == {
        "profile_id": str(pid),
        "max_items": 7,
    }
    assert by_type[JobType.BATCH_MATCH].dedupe_key == f"batch-match:{pid}"

    assert by_type[JobType.GENERATE_COVER_LETTER].payload == {"application_id": str(aid)}
    assert by_type[JobType.GENERATE_COVER_LETTER].dedupe_key == f"generate-cover-letter:{aid}"
