"""Per-(profile, job) match work queue.

Lifecycle:
  enqueue_for_interested_profiles → INSERT pending_match rows for every active
                                    profile whose target_company_slugs.greenhouse
                                    contains the job's company.
  next_batch                      → claim oldest pending_match rows.
  mark_done / mark_attempt_failed → terminal transitions.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.data.slug_company import company_name_to_slug
from app.models.application import Application
from app.models.job import Job

MAX_ATTEMPTS = 3
log = structlog.get_logger()


async def enqueue_for_interested_profiles(job: Job, session: AsyncSession) -> int:
    """For every active profile whose slug list contains job's company,
    INSERT an Application(match_status='pending_match'). Idempotent on
    (job_id, profile_id) — relies on uq_applications_job_profile."""
    slug = company_name_to_slug(job.company_name)
    # Postgres JSONB containment: target_company_slugs->'greenhouse' @> '"<slug>"'::jsonb
    # CAST(... AS jsonb) avoids SQLAlchemy mis-parsing the `::` shortcut as a bind param
    result = await session.execute(
        text(
            "SELECT id FROM user_profiles "
            "WHERE search_active = true "
            "AND target_company_slugs->'greenhouse' @> CAST(:needle AS jsonb)"
        ),
        {"needle": f'"{slug}"'},
    )
    profile_ids = [row[0] for row in result.all()]
    if not profile_ids:
        return 0

    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid.uuid4(),
            "job_id": job.id,
            "profile_id": pid,
            "match_status": "pending_match",
            "match_queued_at": now,
            "match_attempts": 0,
            "status": "pending_review",
            "generation_status": "none",
            "generation_attempts": 0,
            "match_strengths": [],
            "match_gaps": [],
            "created_at": now,
            "updated_at": now,
        }
        for pid in profile_ids
    ]
    stmt = (
        insert(Application)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_applications_job_profile")
    )
    res = await session.execute(stmt)
    await session.commit()
    return res.rowcount or 0


async def next_batch(
    session: AsyncSession, *, limit: int = 30, lease_seconds: int = 300
) -> list[Application]:
    """Claim up to `limit` pending_match rows. Mirrors slug_registry_service.next_pending:
    SELECT … FOR UPDATE SKIP LOCKED so concurrent workers don't fight over the same rows."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    result = await session.execute(
        select(Application)
        .where(
            Application.match_status == "pending_match",
            (Application.match_claimed_at.is_(None)) | (Application.match_claimed_at < cutoff),
        )
        .order_by(Application.match_queued_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    now = datetime.now(UTC)
    for row in rows:
        row.match_claimed_at = now
    if rows:
        await session.commit()
    return rows


async def mark_done(application_id: uuid.UUID, session: AsyncSession) -> None:
    result = await session.execute(select(Application).where(Application.id == application_id))
    app = result.scalar_one()
    app.match_status = "matched"
    app.match_queued_at = None
    app.match_claimed_at = None
    await session.commit()


async def mark_attempt_failed(application_id: uuid.UUID, session: AsyncSession) -> None:
    result = await session.execute(select(Application).where(Application.id == application_id))
    app = result.scalar_one()
    app.match_attempts += 1
    app.match_claimed_at = None
    if app.match_attempts >= MAX_ATTEMPTS:
        app.match_status = "error"
        app.match_queued_at = None
    await session.commit()


async def pending_count(session: AsyncSession, profile_id: uuid.UUID | None = None) -> int:
    from sqlalchemy import func

    q = (
        select(func.count())
        .select_from(Application)
        .where(Application.match_status == "pending_match")
    )
    if profile_id is not None:
        q = q.where(Application.profile_id == profile_id)
    result = await session.execute(q)
    return int(result.scalar_one())
