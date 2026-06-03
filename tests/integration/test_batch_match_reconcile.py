from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models.llm_match_batch import BATCH_STATUS_DONE, BATCH_STATUS_SUBMITTED, LLMMatchBatch
from app.models.work_queue import WorkQueue
from app.services.batch_match_reconcile import enqueue_due_batch_polls


@pytest.mark.asyncio
async def test_enqueue_due_batch_polls_repairs_missing_work_queue_row(db_session, seeded_user):
    _user, profile = seeded_user
    db_session.add(
        LLMMatchBatch(
            profile_id=profile.id,
            provider="fake",
            provider_batch_id="batch-due-service",
            model="gemini-2.5-flash",
            prompt_version="batch-match-v1",
            status=BATCH_STATUS_SUBMITTED,
            next_poll_at=datetime.now(UTC) - timedelta(seconds=5),
        )
    )
    await db_session.commit()

    rows = await enqueue_due_batch_polls(db_session, profile_id=profile.id)
    await db_session.commit()

    assert len(rows) == 1
    queued = (
        await db_session.execute(
            select(WorkQueue).where(
                WorkQueue.job_type == "batch-match",
                WorkQueue.dedupe_key == f"batch-match:{profile.id}",
            )
        )
    ).scalar_one_or_none()
    assert queued is not None


@pytest.mark.asyncio
async def test_enqueue_due_batch_polls_ignores_not_due_and_terminal_batches(
    db_session, seeded_user
):
    _user, profile = seeded_user
    db_session.add_all(
        [
            LLMMatchBatch(
                profile_id=profile.id,
                provider="fake",
                provider_batch_id="batch-future",
                model="gemini-2.5-flash",
                prompt_version="batch-match-v1",
                status=BATCH_STATUS_SUBMITTED,
                next_poll_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            LLMMatchBatch(
                profile_id=profile.id,
                provider="fake",
                provider_batch_id="batch-done",
                model="gemini-2.5-flash",
                prompt_version="batch-match-v1",
                status=BATCH_STATUS_DONE,
                next_poll_at=datetime.now(UTC) - timedelta(minutes=5),
            ),
        ]
    )
    await db_session.commit()

    rows = await enqueue_due_batch_polls(db_session, profile_id=profile.id)

    assert rows == []
