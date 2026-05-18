"""Tests for the Lever postings adapter."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.sources.base import InvalidSlugError, TransientFetchError
from app.sources.lever_postings import LEVER_POSTINGS_BASE, LeverPostingsSource


@pytest.fixture
def src():
    return LeverPostingsSource()


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _posting(idx: int, posted_iso: str = "2026-05-01T12:00:00Z") -> dict:
    return {
        "id": f"posting-{idx}",
        "text": f"Title {idx}",
        "descriptionHtml": f"<p>Body {idx}</p>",
        "descriptionPlain": f"Body {idx}",
        "categories": {
            "location": "Remote — US",
            "team": "Engineering",
            "commitment": "Full-time",
        },
        "hostedUrl": f"https://jobs.lever.co/acme/posting-{idx}",
        "applyUrl": f"https://jobs.lever.co/acme/posting-{idx}/apply",
        "createdAt": _ms(posted_iso),
        "workplaceType": "remote",
    }


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_true_on_200(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(200, json=[])
    async with httpx.AsyncClient() as client:
        assert await src.validate("acme", client=client) is True


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_false_on_404(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        assert await src.validate("missing", client=client) is False


@respx.mock
@pytest.mark.asyncio
async def test_validate_5xx_raises_transient(src):
    """5xx upstream is a transient error, not a confirmed miss. _fan_out
    distinguishes 'error' from False, so the next sync cycle's SlugFetch
    retry can repair the gap if the validate happened during a blip."""
    respx.get(f"{LEVER_POSTINGS_BASE}/flaky").respond(502)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.validate("flaky", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_validate_network_error_raises_transient(src):
    """A connection-level failure is a transient error, not a confirmed miss."""
    respx.get(f"{LEVER_POSTINGS_BASE}/blip").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.validate("blip", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_single_page(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[_posting(1), _posting(2)]),
            httpx.Response(200, json=[]),  # empty page ends the loop
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["posting-1", "posting-2"]
    assert all(j.description_raw == f"<p>Body {i}</p>" for i, j in enumerate(jobs, start=1))
    assert jobs[0].apply_url == "https://jobs.lever.co/acme/posting-1/apply"
    assert jobs[0].workplace_type == "remote"
    assert jobs[0].location == "Remote — US"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_includes_salary_range(src):
    posting = _posting(1)
    posting["salaryRange"] = {
        "currency": "USD",
        "interval": "year",
        "min": 150000,
        "max": 190000,
    }
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[posting]),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert jobs[0].salary == "$150,000–$190,000"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_paginates_until_empty(src):
    page1 = [_posting(i) for i in range(100)]
    page2 = [_posting(i) for i in range(100, 150)]
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert len(jobs) == 150


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_404_raises_invalid_slug(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSlugError):
            await src.fetch_jobs("missing", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_5xx_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(503)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_network_error_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_malformed_json_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(200, content=b"not json")
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_postings_without_apply_url(src):
    bad = _posting(1)
    bad["applyUrl"] = ""
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[bad, _posting(2)]),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["posting-2"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_falls_back_to_description_when_html_missing(src):
    item = _posting(1)
    item.pop("descriptionHtml")
    item["description"] = "<p>Plain</p>"
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[item]),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert jobs[0].description_raw == "<p>Plain</p>"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_filters_by_since(src):
    recent = _posting(1, "2026-05-05T12:00:00Z")
    old = _posting(2, "2025-01-01T00:00:00Z")
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[recent, old]),
            httpx.Response(200, json=[]),
        ]
    )
    cutoff = datetime.now(UTC) - timedelta(days=14)
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", since=cutoff, client=client)
    assert [j.external_id for j in jobs] == ["posting-1"]
