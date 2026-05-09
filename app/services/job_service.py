"""Job CRUD and staleness logic."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.models.job import Job
from app.services.html_cleaner import clean_html_to_markdown
from app.sources.base import JobData


async def _resolve_company_id(
    source: str, slug: str | None, session: AsyncSession
) -> uuid.UUID | None:
    """Look up Company.id by (source, slug). Returns None if no match."""
    if not slug:
        return None
    result = await session.execute(
        select(Company.id).where(Company.provider_slugs[source].astext == slug)
    )
    return result.scalar_one_or_none()


async def upsert_job(
    job_data: JobData,
    source: str,
    session: AsyncSession,
    *,
    slug: str | None = None,
) -> tuple[Job, bool]:
    """
    Insert or update a job. Returns (job, created).
    On conflict (source + external_id): update title, description, is_active, fetched_at.
    description (markdown) is recomputed from description_raw on every write.

    `slug` (when provided) is the (source, slug) pair the fetcher used; it lets us
    populate `Job.company_id` at write time via Company.provider_slugs lookup. The
    matching pipeline (enqueue_for_interested_profiles, match_service) reads the
    new column to find profiles via UserProfile.target_company_ids — D6 closes the
    read-side gap from D4/D5. Without `slug`, company_id stays NULL on insert
    (legacy callers / tests not exercising the matcher).
    """
    result = await session.execute(
        select(Job).where(
            Job.source == source,
            Job.external_id == job_data.external_id,
        )
    )
    existing = result.scalar_one_or_none()

    cleaned = clean_html_to_markdown(job_data.description_raw)
    company_id = await _resolve_company_id(source, slug, session)

    if existing:
        existing.title = job_data.title
        existing.company_name = job_data.company_name
        existing.description_raw = job_data.description_raw
        existing.description = cleaned
        existing.salary = job_data.salary
        existing.contract_type = job_data.contract_type
        existing.apply_url = job_data.apply_url
        existing.location = job_data.location
        existing.workplace_type = job_data.workplace_type
        existing.is_active = True
        existing.fetched_at = datetime.now(UTC)
        # Backfill company_id on legacy rows whose canonical_name didn't match
        # company_name at migration time. Only set, never clear — a slug-less
        # caller must not unlink a previously-resolved Company.
        if company_id is not None and existing.company_id != company_id:
            existing.company_id = company_id
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing, False

    job = Job(
        source=source,
        external_id=job_data.external_id,
        title=job_data.title,
        company_name=job_data.company_name,
        company_id=company_id,
        location=job_data.location,
        workplace_type=job_data.workplace_type,
        description_raw=job_data.description_raw,
        description=cleaned,
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
