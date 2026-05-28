"""Job CRUD and staleness logic."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
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


def compute_job_content_hash(job_data: JobData) -> str:
    payload = {
        "title": job_data.title,
        "company_name": job_data.company_name,
        "location": job_data.location,
        "workplace_type": job_data.workplace_type,
        "description_raw": job_data.description_raw,
        "salary": job_data.salary,
        "contract_type": job_data.contract_type,
        "apply_url": job_data.apply_url,
        "posted_at": job_data.posted_at.isoformat() if job_data.posted_at else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    content_hash = compute_job_content_hash(job_data)
    existing_row = (
        await session.execute(
            select(
                Job.id,
                Job.content_hash,
                Job.company_id,
                Job.company_name,
                Job.source,
            ).where(
                Job.source == source,
                Job.external_id == job_data.external_id,
            )
        )
    ).one_or_none()

    company_id = await _resolve_company_id(source, slug, session)

    if existing_row is not None:
        job_id, existing_hash, existing_company_id, existing_company_name, existing_source = (
            existing_row
        )
        now = datetime.now(UTC)
        values = {
            "is_active": True,
            "fetched_at": now,
        }
        resolved_company_id = existing_company_id
        if company_id is not None and existing_company_id != company_id:
            values["company_id"] = company_id
            resolved_company_id = company_id

        content_changed = existing_hash != content_hash
        cleaned = None
        if content_changed:
            cleaned = clean_html_to_markdown(job_data.description_raw)
            values.update(
                {
                    "title": job_data.title,
                    "company_name": job_data.company_name,
                    "description_raw": job_data.description_raw,
                    "description": cleaned,
                    "salary": job_data.salary,
                    "contract_type": job_data.contract_type,
                    "apply_url": job_data.apply_url,
                    "location": job_data.location,
                    "workplace_type": job_data.workplace_type,
                    "posted_at": job_data.posted_at,
                    "content_hash": content_hash,
                }
            )
        await session.execute(update(Job).where(Job.id == job_id).values(**values))
        await session.commit()
        return (
            Job(
                id=job_id,
                source=existing_source,
                external_id=job_data.external_id,
                title=job_data.title,
                company_name=job_data.company_name if content_changed else existing_company_name,
                company_id=resolved_company_id,
                location=job_data.location,
                workplace_type=job_data.workplace_type,
                description_raw=job_data.description_raw if content_changed else None,
                description=cleaned,
                salary=job_data.salary,
                contract_type=job_data.contract_type,
                apply_url=job_data.apply_url,
                posted_at=job_data.posted_at,
                content_hash=content_hash,
            ),
            False,
        )

    cleaned = clean_html_to_markdown(job_data.description_raw)

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
        content_hash=content_hash,
        salary=job_data.salary,
        contract_type=job_data.contract_type,
        apply_url=job_data.apply_url,
        posted_at=job_data.posted_at,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, True


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
