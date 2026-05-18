from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlmodel import select

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.services.job_salary_backfill import backfill_job_salaries
from app.sources.base import JobData


def _job(
    *,
    source: str = "greenhouse",
    external_id: str = "job-1",
    description_raw: str | None = None,
    salary: str | None = None,
    company_id=None,
) -> Job:
    return Job(
        source=source,
        external_id=external_id,
        title=f"Title {external_id}",
        company_name="Acme",
        company_id=company_id,
        description_raw=description_raw,
        description="",
        salary=salary,
        apply_url=f"https://example.test/{external_id}",
        posted_at=datetime.now(UTC),
    )


async def _matched_job(db_session, seeded_user, job: Job) -> None:
    _, profile = seeded_user
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    db_session.add(Application(job_id=job.id, profile_id=profile.id, match_score=0.9))
    await db_session.commit()


@pytest.mark.asyncio
async def test_backfill_job_salaries_updates_existing_matches_from_description(
    db_session, seeded_user
):
    job = _job(description_raw="<p>Base salary range: $150,000 - $190,000.</p>")
    already_has_salary = _job(
        external_id="job-2",
        description_raw="<p>Base salary range: $120,000 - $150,000.</p>",
        salary="$1",
    )
    no_salary = _job(external_id="job-3", description_raw="<p>No range here.</p>")
    unmatched = _job(
        external_id="job-4",
        description_raw="<p>Base salary range: $100,000 - $120,000.</p>",
    )
    await _matched_job(db_session, seeded_user, job)
    await _matched_job(db_session, seeded_user, already_has_salary)
    await _matched_job(db_session, seeded_user, no_salary)
    db_session.add(unmatched)
    await db_session.commit()

    result = await backfill_job_salaries(db_session, apply=True, fetch_structured=False)

    assert result.scanned == 2
    assert result.updated == 1
    assert result.from_description == 1
    await db_session.refresh(job)
    await db_session.refresh(already_has_salary)
    await db_session.refresh(no_salary)
    await db_session.refresh(unmatched)
    assert job.salary == "$150,000 - $190,000"
    assert already_has_salary.salary == "$1"
    assert no_salary.salary is None
    assert unmatched.salary is None


@pytest.mark.asyncio
async def test_backfill_job_salaries_dry_run_does_not_write(db_session, seeded_user):
    job = _job(description_raw="<p>Compensation: $150,000 - $190,000.</p>")
    await _matched_job(db_session, seeded_user, job)

    result = await backfill_job_salaries(db_session, apply=False, fetch_structured=False)

    assert result.updated == 1
    await db_session.refresh(job)
    assert job.salary is None


@pytest.mark.asyncio
async def test_backfill_job_salaries_refetches_full_slug_for_structured_compensation(
    db_session, seeded_user, monkeypatch
):
    company = Company(
        canonical_name="Acme",
        normalized_key=f"acme-{uuid4()}",
        provider_slugs={"ashby": "acme"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    job = _job(
        source="ashby",
        external_id="https://jobs.ashbyhq.com/acme/job-1",
        description_raw="<p>No salary in description.</p>",
        company_id=company.id,
    )
    await _matched_job(db_session, seeded_user, job)

    class FakeAshbySource:
        async def fetch_jobs(self, slug, *, since=None, client=None):
            assert slug == "acme"
            assert since is None
            return [
                JobData(
                    external_id="https://jobs.ashbyhq.com/acme/job-1",
                    title="Title",
                    company_name="Acme",
                    description_raw="<p>No salary in description.</p>",
                    salary="$150,000–$190,000",
                    apply_url="https://jobs.ashbyhq.com/acme/job-1/application",
                )
            ]

    import app.services.job_salary_backfill as salary_backfill

    monkeypatch.setitem(salary_backfill.SOURCES, "ashby", FakeAshbySource())

    result = await backfill_job_salaries(db_session, apply=True, fetch_structured=True)

    assert result.scanned == 1
    assert result.updated == 1
    assert result.from_refetch == 1
    refreshed = (await db_session.execute(select(Job).where(Job.id == job.id))).scalar_one()
    assert refreshed.salary == "$150,000–$190,000"
