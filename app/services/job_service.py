"""Job CRUD and staleness logic."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.job import Job
from app.sources.base import JobData


async def upsert_job(job_data: JobData, source: str, session: AsyncSession) -> tuple[Job, bool]:
    """
    Insert or update a job. Returns (job, created).
    On conflict (source + external_id): update title, description, is_active, fetched_at.
    """
    result = await session.execute(
        select(Job).where(
            Job.source == source,
            Job.external_id == job_data.external_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.title = job_data.title
        existing.company_name = job_data.company_name
        existing.description_md = job_data.description_md
        existing.salary = job_data.salary
        existing.contract_type = job_data.contract_type
        existing.apply_url = job_data.apply_url
        existing.location = job_data.location
        existing.workplace_type = job_data.workplace_type
        existing.is_active = True
        existing.fetched_at = datetime.now(UTC)
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing, False

    job = Job(
        source=source,
        external_id=job_data.external_id,
        title=job_data.title,
        company_name=job_data.company_name,
        location=job_data.location,
        workplace_type=job_data.workplace_type,
        description_md=job_data.description_md,
        salary=job_data.salary,
        contract_type=job_data.contract_type,
        apply_url=job_data.apply_url,
        posted_at=job_data.posted_at,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, True


async def get_active_jobs(
    session: AsyncSession,
    source: str | None = None,
    workplace_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    q = select(Job).where(Job.is_active.is_(True))
    if source:
        q = q.where(Job.source == source)
    if workplace_type:
        q = q.where(Job.workplace_type == workplace_type)
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def mark_stale_jobs(stale_after_days: int, session: AsyncSession) -> int:
    """Mark jobs as inactive if not refreshed within stale_after_days. Returns count."""
    cutoff = datetime.now(UTC) - timedelta(days=stale_after_days)
    result = await session.execute(
        select(Job).where(
            Job.is_active.is_(True),
            Job.fetched_at < cutoff,
        )
    )
    jobs = list(result.scalars().all())
    for job in jobs:
        job.is_active = False
        session.add(job)
    if jobs:
        await session.commit()
    return len(jobs)
