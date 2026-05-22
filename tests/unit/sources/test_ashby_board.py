"""Tests for the Ashby board adapter."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.sources.ashby_board import ASHBY_POSTINGS_BASE, AshbyBoardSource
from app.sources.base import InvalidSlugError, TransientFetchError


@pytest.fixture
def src():
    return AshbyBoardSource()


def _posting(idx: int, posted_iso: str = "2026-05-01T12:00:00Z") -> dict:
    return {
        "title": f"Title {idx}",
        "department": "Engineering",
        "team": "Platform",
        "descriptionHtml": f"<p>Body {idx}</p>",
        "descriptionPlain": f"Body {idx}",
        "publishedAt": posted_iso,
        "employmentType": "FullTime",
        "jobUrl": f"https://jobs.ashbyhq.com/acme/job-{idx}?utm_source=board",
        "applyUrl": f"https://jobs.ashbyhq.com/acme/job-{idx}/application",
        "isListed": True,
        "workplaceType": "Remote",
        "location": "San Francisco, CA",
        "secondaryLocations": [],
        "address": {},
    }


def _payload(*postings: dict) -> dict:
    return {"jobs": list(postings)}


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_true_on_200(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload())
    async with httpx.AsyncClient() as client:
        assert await src.validate("acme", client=client) is True


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_false_on_404(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        assert await src.validate("missing", client=client) is False


@respx.mock
@pytest.mark.asyncio
async def test_validate_5xx_raises_transient(src):
    """5xx upstream is a transient error, not a confirmed miss. _fan_out
    distinguishes 'error' from False, so the next sync cycle's SlugFetch
    retry can repair the gap if the validate happened during a blip."""
    respx.get(f"{ASHBY_POSTINGS_BASE}/flaky").respond(503)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.validate("flaky", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_validate_network_error_raises_transient(src):
    """A connection-level failure is a transient error, not a confirmed miss."""
    respx.get(f"{ASHBY_POSTINGS_BASE}/blip").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.validate("blip", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_happy_path(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(_posting(1), _posting(2)))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert len(jobs) == 2
    assert jobs[0].description_raw == "<p>Body 1</p>"
    # external_id is the jobUrl with tracking params stripped.
    assert jobs[0].external_id == "https://jobs.ashbyhq.com/acme/job-1"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_includes_ashby_compensation_salary_summary(src):
    posting = _posting(1)
    posting["compensation"] = {
        "compensationTierSummary": "$81K - $87K • 0.5% - 1.75%",
        "scrapeableCompensationSalarySummary": "$81K - $87K",
        "summaryComponents": [
            {
                "compensationType": "Salary",
                "interval": "1 YEAR",
                "currencyCode": "USD",
                "minValue": 81000,
                "maxValue": 87000,
            }
        ],
    }
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(posting))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert jobs[0].salary == "$81K - $87K"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_ignores_non_salary_compensation_summary(src):
    posting = _posting(1)
    posting["compensation"] = {
        "scrapeableCompensationSalarySummary": "Salary",
        "summaryComponents": [
            {
                "compensationType": "Salary",
                "interval": "1 YEAR",
                "minValue": 0,
                "maxValue": 0,
            }
        ],
    }
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(posting))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert jobs[0].salary is None


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_extracts_salary_range_from_description(src):
    posting = _posting(1)
    posting["descriptionHtml"] = "<p>The salary range for this role is $150,000 - $190,000.</p>"
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(posting))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert jobs[0].salary == "$150,000 - $190,000"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_postings_without_apply_url(src):
    bad = _posting(1)
    bad["applyUrl"] = ""
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(bad, _posting(2)))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["https://jobs.ashbyhq.com/acme/job-2"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_404_raises_invalid_slug(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSlugError):
            await src.fetch_jobs("missing", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_5xx_raises_transient(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(502)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_filters_by_since(src):
    recent_posted_at = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace(
        "+00:00",
        "Z",
    )
    recent = _posting(1, recent_posted_at)
    old = _posting(2, "2025-01-01T00:00:00Z")
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(recent, old))
    cutoff = datetime.now(UTC) - timedelta(days=14)
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", since=cutoff, client=client)
    assert [j.title for j in jobs] == ["Title 1"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_unlisted(src):
    unlisted = _posting(1)
    unlisted["isListed"] = False
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(unlisted, _posting(2)))
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.title for j in jobs] == ["Title 2"]
