"""Unit tests for the Remotive job source adapter."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.sources.remotive import REMOTIVE_BASE_URL, RemotiveSource

FIXTURE_RESPONSE = {
    "jobs": [
        {
            "id": 12345,
            "url": "https://remotive.com/remote-jobs/engineering/swe-12345",
            "title": "Software Engineer",
            "company_name": "Acme Corp",
            "candidate_required_location": "USA Only",
            "salary": "$100k-$150k",
            "description": "<p>Build great things.</p>",
            "job_type": "full_time",
            "publication_date": "2026-04-01T12:00:00",
        }
    ]
}

GREENHOUSE_FIXTURE_RESPONSE = {
    "jobs": [
        {
            "id": 99999,
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "title": "Backend Engineer",
            "company_name": "Acme Corp",
            "candidate_required_location": "Worldwide",
            "salary": "",
            "description": "<p>Work on our platform.</p>",
            "job_type": "full_time",
            "publication_date": "2026-04-01T12:00:00",
        }
    ]
}


def make_settings(max_results: int = 50) -> MagicMock:
    settings = MagicMock()
    settings.remotive_max_results = max_results
    settings.adzuna_cache_ttl_hours = 24
    return settings


def no_cache_source(monkeypatch) -> RemotiveSource:
    source = RemotiveSource()
    monkeypatch.setattr(source, "_get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(source, "_save_cache", AsyncMock(return_value=None))
    return source


@pytest.mark.asyncio
async def test_remotive_happy_path(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTIVE_BASE_URL).mock(return_value=httpx.Response(200, json=FIXTURE_RESPONSE))
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert cursor is None
    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "12345"
    assert job.title == "Software Engineer"
    assert job.company_name == "Acme Corp"
    assert job.workplace_type == "remote"
    assert job.contract_type == "full_time"
    assert job.location == "USA Only"
    assert job.salary == "$100k-$150k"
    assert job.description_md == "<p>Build great things.</p>"
    assert job.apply_url == "https://remotive.com/remote-jobs/engineering/swe-12345"
    assert job.posted_at == datetime(2026, 4, 1, 12, 0, 0)


@pytest.mark.asyncio
async def test_remotive_empty_jobs(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTIVE_BASE_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_remotive_http_error(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTIVE_BASE_URL).mock(return_value=httpx.Response(500))
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_remotive_connection_error(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTIVE_BASE_URL).mock(side_effect=httpx.ConnectError("refused"))
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_remotive_ats_detection_applied(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTIVE_BASE_URL).mock(
            return_value=httpx.Response(200, json=GREENHOUSE_FIXTURE_RESPONSE)
        )
        jobs, _ = await source.search("python", None, None, settings, MagicMock())

    assert len(jobs) == 1
    job = jobs[0]
    assert job.ats_type == "greenhouse"
    assert job.supports_api_apply is True
