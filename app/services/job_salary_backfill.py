"""Backfill salary fields for already-matched jobs without re-scoring."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx
from sqlalchemy import exists
from sqlmodel import col, select

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.sources import SOURCES
from app.sources.greenhouse_board import DEFAULT_TIMEOUT
from app.sources.salary import extract_salary_range_from_text


@dataclass
class SalaryBackfillResult:
    scanned: int = 0
    updated: int = 0
    from_description: int = 0
    from_refetch: int = 0
    unchanged: int = 0
    failed_refetches: list[str] = field(default_factory=list)


def _candidate_query(limit: int | None = None):
    query = (
        select(Job)
        .where(
            col(Job.salary).is_(None),
            exists().where(Application.job_id == Job.id),
        )
        .order_by(Job.fetched_at.desc(), Job.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return query


async def _load_candidate_jobs(session, *, limit: int | None) -> list[Job]:
    rows = await session.execute(_candidate_query(limit))
    return list(rows.scalars().all())


async def _companies_by_id(session, jobs: Iterable[Job]) -> dict:
    company_ids = {job.company_id for job in jobs if job.company_id is not None}
    if not company_ids:
        return {}
    rows = await session.execute(select(Company).where(col(Company.id).in_(company_ids)))
    return {company.id: company for company in rows.scalars().all()}


async def _refetch_salary_map(groups: dict[tuple[str, str], list[Job]]) -> tuple[dict, list[str]]:
    salary_by_key: dict[tuple[str, str], str] = {}
    failed_refetches: list[str] = []

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for (source, slug), jobs in groups.items():
            adapter = SOURCES.get(source)
            if adapter is None:
                failed_refetches.append(f"{source}:{slug}: unknown provider")
                continue
            try:
                fetched_jobs = await adapter.fetch_jobs(slug, since=None, client=client)
            except Exception as exc:  # noqa: BLE001 - report and keep the backfill moving.
                failed_refetches.append(f"{source}:{slug}: {type(exc).__name__}: {exc}")
                continue

            wanted_external_ids = {job.external_id for job in jobs}
            for fetched in fetched_jobs:
                if fetched.external_id in wanted_external_ids and fetched.salary:
                    salary_by_key[(source, fetched.external_id)] = fetched.salary

    return salary_by_key, failed_refetches


async def backfill_job_salaries(
    session,
    *,
    apply: bool = False,
    fetch_structured: bool = True,
    limit: int | None = None,
) -> SalaryBackfillResult:
    """Populate Job.salary for jobs that already have Application rows.

    This never calls the matching agent, clears scores, or enqueues match jobs.
    It first extracts salary ranges from stored description_raw. If requested,
    it then refetches each provider slug once and copies structured salary
    values onto matching existing jobs.
    """
    jobs = await _load_candidate_jobs(session, limit=limit)
    result = SalaryBackfillResult(scanned=len(jobs))

    remaining: list[Job] = []
    for job in jobs:
        salary = extract_salary_range_from_text(job.description_raw)
        if salary:
            result.updated += 1
            result.from_description += 1
            if apply:
                job.salary = salary
                session.add(job)
        else:
            remaining.append(job)

    if fetch_structured and remaining:
        companies = await _companies_by_id(session, remaining)
        groups: dict[tuple[str, str], list[Job]] = {}
        for job in remaining:
            company = companies.get(job.company_id)
            if company is None:
                continue
            slug = (company.provider_slugs or {}).get(job.source)
            if not slug:
                continue
            groups.setdefault((job.source, slug), []).append(job)

        salary_by_key, failed_refetches = await _refetch_salary_map(groups)
        result.failed_refetches.extend(failed_refetches)

        for job in remaining:
            salary = salary_by_key.get((job.source, job.external_id))
            if salary is None:
                continue
            result.updated += 1
            result.from_refetch += 1
            if apply:
                job.salary = salary
                session.add(job)

    result.unchanged = result.scanned - result.updated
    if apply and result.updated:
        await session.commit()
    else:
        await session.rollback()
    return result
