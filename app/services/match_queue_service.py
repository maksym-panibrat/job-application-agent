"""Per-(profile, job) match work queue.

Lifecycle:
  enqueue_for_interested_profiles → INSERT pending_match rows for every active
                                    profile whose target_company_ids array
                                    contains the job's Company.
  next_batch                      → claim oldest pending_match rows.
  mark_done / mark_attempt_failed → terminal transitions.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.data.slug_company import company_name_to_slug
from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user_profile import UserProfile

MAX_ATTEMPTS = 3
log = structlog.get_logger()


async def enqueue_for_interested_profiles(job: Job, session: AsyncSession) -> int:
    """For every active profile whose target_company_ids array contains the
    job's Company, INSERT an Application(match_status='pending_match'). Idempotent
    on (job_id, profile_id) — relies on uq_applications_job_profile.

    Two paths:
      1. job.company_id set (the post-D6 norm — populated by job_service.upsert_job
         from the (source, slug) pair at write time): direct ARRAY @> match.
      2. job.company_id is NULL (legacy rows whose canonical_name didn't equal
         company_name at migration time): fall back to looking up Company by
         (source, derived_slug) and matching that. This branch is the
         belt-and-suspenders path — once every active job has been re-fetched
         once after D6 deploy, every row has company_id set.
    """
    company_id = job.company_id
    if company_id is None:
        # Legacy fallback: derive slug from company_name and resolve Company.id.
        slug = company_name_to_slug(job.company_name)
        resolved = await session.execute(
            select(Company.id).where(Company.provider_slugs[job.source].astext == slug)
        )
        company_id = resolved.scalar_one_or_none()
        if company_id is None:
            return 0

    result = await session.execute(
        select(UserProfile.id).where(
            UserProfile.search_active.is_(True),
            UserProfile.target_company_ids.contains([company_id]),
        )
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


async def audit_error_apps(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    profile_id: uuid.UUID | None = None,
) -> list[dict]:
    """Count Application rows in match_status='error', grouped by profile_id.

    Used for one-off recovery after incidents that wrongly drove apps to
    'error' (e.g. the 2026-05-04 Gemini credit depletion: while the new
    BudgetExhausted graceful path wasn't yet deployed, every match_queue tick
    incremented attempts on every claimed app; after 3 attempts → error).

    `since` filters by Application.created_at; passing None counts all.
    """
    from sqlalchemy import func

    q = select(
        Application.profile_id,
        func.count(Application.id).label("count"),
        func.min(Application.created_at).label("oldest"),
        func.max(Application.created_at).label("newest"),
    ).where(Application.match_status == "error")
    if since is not None:
        q = q.where(Application.created_at >= since)
    if profile_id is not None:
        q = q.where(Application.profile_id == profile_id)
    q = q.group_by(Application.profile_id)

    result = await session.execute(q)
    return [
        {
            "profile_id": row.profile_id,
            "count": row.count,
            "oldest": row.oldest,
            "newest": row.newest,
        }
        for row in result.all()
    ]


async def recover_error_apps(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    profile_id: uuid.UUID | None = None,
) -> int:
    """Re-queue Application rows in match_status='error' back to pending_match.

    Resets the lifecycle so run_match_queue picks them up on the next tick:
      match_status='error' → 'pending_match'
      match_attempts → 0
      match_queued_at → now()
      match_claimed_at → None
      match_score / match_summary / match_rationale: untouched (already null
      on error rows by construction — they never scored successfully).

    Returns the rowcount.
    """
    now = datetime.now(UTC)
    from sqlalchemy import update

    stmt = (
        update(Application)
        .where(Application.match_status == "error")
        .values(
            match_status="pending_match",
            match_attempts=0,
            match_queued_at=now,
            match_claimed_at=None,
            updated_at=now,
        )
    )
    if since is not None:
        stmt = stmt.where(Application.created_at >= since)
    if profile_id is not None:
        stmt = stmt.where(Application.profile_id == profile_id)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount or 0


async def release_claim(application_id: uuid.UUID, session: AsyncSession) -> None:
    """Clear the lease without incrementing attempts.

    Used when scoring failed for a reason that's not the app's fault — e.g.
    BudgetExhausted (Gemini quota gone). The app stays pending_match and
    becomes eligible for the next tick to re-claim once the underlying issue
    resolves. Contrast with `mark_attempt_failed` which increments attempts
    and after MAX_ATTEMPTS flips the app to status='error'."""
    result = await session.execute(select(Application).where(Application.id == application_id))
    app = result.scalar_one()
    app.match_claimed_at = None
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
