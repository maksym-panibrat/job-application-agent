"""Plain-SQL work_queue operations. Caller controls commit boundaries."""
import json
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work_queue import WorkQueue


class StaleLease(Exception):
    """A finalizer was called by a worker that no longer owns the row lease."""


async def enqueue(
    session: AsyncSession,
    *,
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    on_conflict: Literal["do_nothing", "upsert_reset_not_before"] = "do_nothing",
    not_before: datetime | None = None,
) -> int | None:
    """Insert a pending queue row, returning either the new row id or the live
    row id that blocked a deduped insert.
    """
    params = {
        "job_type": job_type,
        "payload": json.dumps(payload, default=str),
        "dedupe_key": dedupe_key,
        "not_before": not_before,
    }
    if on_conflict == "upsert_reset_not_before":
        result = await session.execute(
            text(
                """
                INSERT INTO work_queue (job_type, payload, dedupe_key, not_before)
                VALUES (
                    :job_type,
                    CAST(:payload AS jsonb),
                    :dedupe_key,
                    :not_before
                )
                ON CONFLICT (job_type, dedupe_key)
                    WHERE status IN ('pending', 'in_progress')
                      AND dedupe_key IS NOT NULL
                DO UPDATE SET
                    not_before = EXCLUDED.not_before,
                    payload = EXCLUDED.payload
                    WHERE work_queue.status = 'pending'
                RETURNING id
                """
            ),
            params,
        )
    else:
        result = await session.execute(
            text(
                """
                INSERT INTO work_queue (job_type, payload, dedupe_key, not_before)
                VALUES (
                    :job_type,
                    CAST(:payload AS jsonb),
                    :dedupe_key,
                    :not_before
                )
                ON CONFLICT (job_type, dedupe_key)
                    WHERE status IN ('pending', 'in_progress')
                      AND dedupe_key IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            params,
        )

    row = result.first()
    if row is not None:
        return row[0]
    if dedupe_key is None:
        return None

    existing = await session.execute(
        text(
            """
            SELECT id
            FROM work_queue
            WHERE job_type = :job_type
              AND dedupe_key = :dedupe_key
              AND status IN ('pending', 'in_progress')
            LIMIT 1
            """
        ),
        {"job_type": job_type, "dedupe_key": dedupe_key},
    )
    existing_row = existing.first()
    return existing_row[0] if existing_row is not None else None


async def claim_one(
    session: AsyncSession,
    *,
    worker_id: str,
    visibility_timeout_s: int,
    job_types: list[str] | tuple[str, ...] | None = None,
) -> WorkQueue | None:
    """Atomically claim the next pending or stale non-self in-progress row."""
    params: dict[str, Any] = {"timeout": visibility_timeout_s, "worker_id": worker_id}
    job_type_filter = ""
    if job_types is not None:
        allowed_job_types = [
            normalized for job_type in job_types if (normalized := job_type.strip())
        ]
        if not allowed_job_types:
            return None
        params["job_types"] = allowed_job_types
        job_type_filter = "AND job_type = ANY(CAST(:job_types AS text[]))"

    result = await session.execute(
        text(
            f"""
            WITH claimed AS (
              SELECT id
              FROM work_queue
              WHERE (not_before IS NULL OR not_before <= now())
                {job_type_filter}
                AND (
                  status = 'pending'
                  OR (
                    status = 'in_progress'
                    AND claimed_at < now() - make_interval(secs => :timeout)
                    AND (claimed_by IS NULL OR claimed_by <> :worker_id)
                  )
                )
              ORDER BY
                CASE job_type
                  WHEN 'generate-cover-letter' THEN 0
                  WHEN 'fetch-slug' THEN 1
                  WHEN 'maintenance' THEN 2
                  WHEN 'batch-match' THEN 3
                  WHEN 'match' THEN 4
                  ELSE 5
                END ASC,
                enqueued_at ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE work_queue
            SET status = 'in_progress',
                claimed_at = now(),
                claimed_by = :worker_id,
                attempts = work_queue.attempts + 1
            FROM claimed
            WHERE work_queue.id = claimed.id
            RETURNING work_queue.id, work_queue.job_type, work_queue.payload,
                      work_queue.status, work_queue.enqueued_at,
                      work_queue.claimed_at, work_queue.claimed_by,
                      work_queue.not_before, work_queue.completed_at,
                      work_queue.attempts, work_queue.last_error,
                      work_queue.dedupe_key
            """
        ),
        params,
    )
    row = result.first()
    if row is None:
        return None
    return WorkQueue(
        id=row[0],
        job_type=row[1],
        payload=row[2],
        status=row[3],
        enqueued_at=row[4],
        claimed_at=row[5],
        claimed_by=row[6],
        not_before=row[7],
        completed_at=row[8],
        attempts=row[9],
        last_error=row[10],
        dedupe_key=row[11],
    )


async def mark_done(session: AsyncSession, job_id: int, *, worker_id: str) -> None:
    result = await session.execute(
        text(
            """
            UPDATE work_queue
            SET status='done',
                completed_at=now(),
                last_error=NULL,
                claimed_by=NULL
            WHERE id = :id
              AND claimed_by = :worker_id
              AND status = 'in_progress'
            """
        ),
        {"id": job_id, "worker_id": worker_id},
    )
    if result.rowcount == 0:
        raise StaleLease(f"mark_done: row {job_id} no longer owned by {worker_id}")


async def mark_failed(
    session: AsyncSession, job_id: int, *, error: str, worker_id: str
) -> None:
    result = await session.execute(
        text(
            """
            UPDATE work_queue
            SET status='failed',
                completed_at=now(),
                last_error=:error,
                claimed_by=NULL
            WHERE id = :id
              AND claimed_by = :worker_id
              AND status = 'in_progress'
            """
        ),
        {"id": job_id, "error": error[:8000], "worker_id": worker_id},
    )
    if result.rowcount == 0:
        raise StaleLease(f"mark_failed: row {job_id} no longer owned by {worker_id}")


async def release_with_backoff(
    session: AsyncSession,
    job_id: int,
    *,
    seconds: int,
    worker_id: str,
) -> None:
    """Return a leased row to pending without bumping attempts."""
    result = await session.execute(
        text(
            """
            UPDATE work_queue
            SET status='pending',
                claimed_at=NULL,
                claimed_by=NULL,
                not_before = now() + make_interval(secs => :secs)
            WHERE id = :id
              AND claimed_by = :worker_id
              AND status = 'in_progress'
            """
        ),
        {"id": job_id, "secs": seconds, "worker_id": worker_id},
    )
    if result.rowcount == 0:
        raise StaleLease(
            f"release_with_backoff: row {job_id} no longer owned by {worker_id}"
        )
