import pytest
from sqlalchemy import text
from sqlmodel import select

from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.queue_service import (
    StaleLease,
    claim_one,
    enqueue,
    mark_done,
    mark_failed,
    release_with_backoff,
)


@pytest.mark.asyncio
async def test_enqueue_pending_row(db_session):
    row_id = await enqueue(
        db_session,
        job_type="fetch-slug",
        payload={"provider": "greenhouse", "slug": "openai"},
        dedupe_key="fetch-slug:greenhouse:openai",
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.PENDING
    assert row.attempts == 0
    assert row.payload == {"provider": "greenhouse", "slug": "openai"}


@pytest.mark.asyncio
async def test_enqueue_duplicate_dedupe_do_nothing_returns_existing_id(db_session):
    first = await enqueue(
        db_session,
        job_type="fetch-slug",
        payload={"provider": "greenhouse", "slug": "a"},
        dedupe_key="dup-key",
    )
    second = await enqueue(
        db_session,
        job_type="fetch-slug",
        payload={"provider": "greenhouse", "slug": "b"},
        dedupe_key="dup-key",
    )
    await db_session.commit()

    assert second == first
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM work_queue WHERE dedupe_key = 'dup-key'")
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_upsert_reset_not_before_updates_pending_row(db_session):
    row_id = await enqueue(
        db_session,
        job_type="generate-cover-letter",
        payload={"application_id": "old"},
        dedupe_key="generate-cover-letter:app-1",
    )
    await db_session.execute(
        text(
            "UPDATE work_queue SET not_before = now() + interval '5 minutes' "
            "WHERE id = :id"
        ),
        {"id": row_id},
    )
    await db_session.commit()

    second = await enqueue(
        db_session,
        job_type="generate-cover-letter",
        payload={"application_id": "new"},
        dedupe_key="generate-cover-letter:app-1",
        on_conflict="upsert_reset_not_before",
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert second == row_id
    assert row.not_before is None
    assert row.payload == {"application_id": "new"}


@pytest.mark.asyncio
async def test_claim_one_picks_oldest_pending_row(db_session):
    await enqueue(db_session, job_type="x", payload={"order": 2})
    first = await enqueue(db_session, job_type="x", payload={"order": 1})
    await db_session.execute(
        text("UPDATE work_queue SET enqueued_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": first},
    )
    await db_session.commit()

    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)
    await db_session.commit()

    assert claimed is not None
    assert claimed.id == first
    assert claimed.status == WorkQueueStatus.IN_PROGRESS
    assert claimed.attempts == 1
    assert claimed.claimed_by == "w1"


@pytest.mark.asyncio
async def test_claim_one_empty_returns_none(db_session):
    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_one_skips_not_before_in_future(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.execute(
        text(
            "UPDATE work_queue SET not_before = now() + interval '5 minutes' "
            "WHERE id = :id"
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)

    assert claimed is None


@pytest.mark.asyncio
async def test_visibility_timeout_reclaims_other_worker_row(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.execute(
        text(
            """
            UPDATE work_queue
            SET status='in_progress',
                claimed_at = now() - interval '700 seconds',
                claimed_by = 'dead-worker',
                attempts = 1
            WHERE id = :id
            """
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(db_session, worker_id="w-new", visibility_timeout_s=600)
    await db_session.commit()

    assert claimed is not None
    assert claimed.id == row_id
    assert claimed.claimed_by == "w-new"
    assert claimed.attempts == 2


@pytest.mark.asyncio
async def test_claim_one_does_not_reclaim_same_worker_stale_row(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.execute(
        text(
            """
            UPDATE work_queue
            SET status='in_progress',
                claimed_at = now() - interval '700 seconds',
                claimed_by = 'me',
                attempts = 1
            WHERE id = :id
            """
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(db_session, worker_id="me", visibility_timeout_s=600)

    assert claimed is None


@pytest.mark.asyncio
async def test_mark_done_requires_lease_owner(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.commit()
    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)

    await mark_done(db_session, claimed.id, worker_id="w1")
    await db_session.commit()

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.DONE
    assert row.completed_at is not None
    assert row.claimed_by is None


@pytest.mark.asyncio
async def test_mark_done_raises_stale_lease_when_reclaimed(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.commit()
    claimed_by_a = await claim_one(db_session, worker_id="worker-a", visibility_timeout_s=600)
    await db_session.execute(
        text(
            "UPDATE work_queue SET claimed_at = now() - interval '700 seconds' "
            "WHERE id = :id"
        ),
        {"id": row_id},
    )
    await db_session.commit()
    claimed_by_b = await claim_one(db_session, worker_id="worker-b", visibility_timeout_s=600)
    await mark_done(db_session, claimed_by_b.id, worker_id="worker-b")
    await db_session.commit()

    with pytest.raises(StaleLease):
        await mark_done(db_session, claimed_by_a.id, worker_id="worker-a")

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.DONE


@pytest.mark.asyncio
async def test_mark_failed_requires_lease_owner(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.commit()
    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)

    await mark_failed(db_session, claimed.id, error="boom", worker_id="w1")
    await db_session.commit()

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.FAILED
    assert row.last_error == "boom"
    assert row.claimed_by is None


@pytest.mark.asyncio
async def test_release_with_backoff_requires_lease_owner(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.commit()
    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)
    pre_attempts = claimed.attempts

    await release_with_backoff(db_session, claimed.id, seconds=30, worker_id="w1")
    await db_session.commit()

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.PENDING
    assert row.attempts == pre_attempts
    assert row.not_before is not None
    assert row.claimed_at is None
    assert row.claimed_by is None


@pytest.mark.asyncio
async def test_release_with_backoff_raises_stale_lease_for_wrong_worker(db_session):
    row_id = await enqueue(db_session, job_type="x", payload={})
    await db_session.commit()
    claimed = await claim_one(db_session, worker_id="w1", visibility_timeout_s=600)

    with pytest.raises(StaleLease):
        await release_with_backoff(db_session, claimed.id, seconds=30, worker_id="w2")

    row = (
        await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))
    ).scalar_one()
    assert row.status == WorkQueueStatus.IN_PROGRESS
    assert row.claimed_by == "w1"
