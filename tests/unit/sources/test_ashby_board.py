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
    recent = _posting(1, "2026-05-05T12:00:00Z")
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
