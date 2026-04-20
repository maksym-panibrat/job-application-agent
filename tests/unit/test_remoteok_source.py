"""Unit tests for the RemoteOK job source adapter."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.sources.remoteok import REMOTEOK_BASE_URL, RemoteOKSource

FIXTURE_RESPONSE = [
    {"legal": "all jobs from remoteok.com"},
    {
        "id": "99999",
        "position": "Backend Engineer",
        "company": "TechCorp",
        "location": "",
        "description": "<p>Cool job</p>",
        "url": "https://remoteok.com/jobs/99999",
        "epoch": 1745000000,
        "salary_min": 80000,
        "salary_max": 120000,
        "tags": ["python", "backend"],
    },
]


def make_settings() -> MagicMock:
    settings = MagicMock()
    settings.remoteok_user_agent = (
        "job-application-agent/1.0 (+https://github.com/panibrat/job-application-agent)"
    )
    settings.adzuna_cache_ttl_hours = 24
    return settings


def no_cache_source(monkeypatch) -> RemoteOKSource:
    source = RemoteOKSource()
    monkeypatch.setattr(source, "_get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(source, "_save_cache", AsyncMock(return_value=None))
    return source


@pytest.mark.asyncio
async def test_remoteok_happy_path(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTEOK_BASE_URL).mock(
            return_value=httpx.Response(200, json=FIXTURE_RESPONSE)
        )
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert cursor is None
    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "99999"
    assert job.workplace_type == "remote"
    assert job.salary == "$80,000 – $120,000"
    assert job.location is None
    assert job.posted_at is not None
    assert job.posted_at.tzinfo == UTC


@pytest.mark.asyncio
async def test_remoteok_user_agent_sent(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        route = respx.get(REMOTEOK_BASE_URL).mock(
            return_value=httpx.Response(200, json=FIXTURE_RESPONSE)
        )
        await source.search("python", None, None, settings, MagicMock())

    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers["user-agent"] == settings.remoteok_user_agent


@pytest.mark.asyncio
async def test_remoteok_query_filter(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    fixture = [
        {"legal": "metadata"},
        {
            "id": "1",
            "position": "Python Developer",
            "company": "A",
            "location": "",
            "description": "",
            "url": "https://remoteok.com/jobs/1",
            "epoch": 1745000000,
            "tags": ["python"],
        },
        {
            "id": "2",
            "position": "UI Designer",
            "company": "B",
            "location": "",
            "description": "",
            "url": "https://remoteok.com/jobs/2",
            "epoch": 1745000000,
            "tags": ["design"],
        },
    ]

    with respx.mock:
        respx.get(REMOTEOK_BASE_URL).mock(
            return_value=httpx.Response(200, json=fixture)
        )
        jobs, _ = await source.search("python backend", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert jobs[0].external_id == "1"


@pytest.mark.asyncio
async def test_remoteok_http_403(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(REMOTEOK_BASE_URL).mock(return_value=httpx.Response(403))
        jobs, cursor = await source.search("python", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_remoteok_salary_missing(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    fixture = [
        {"legal": "metadata"},
        {
            "id": "1",
            "position": "Backend Engineer",
            "company": "TechCorp",
            "location": "",
            "description": "<p>Cool job</p>",
            "url": "https://remoteok.com/jobs/1",
            "epoch": 1745000000,
            "salary_min": 0,
            "salary_max": 0,
            "tags": ["python"],
        },
    ]

    with respx.mock:
        respx.get(REMOTEOK_BASE_URL).mock(
            return_value=httpx.Response(200, json=fixture)
        )
        jobs, _ = await source.search("python", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert jobs[0].salary is None


@pytest.mark.asyncio
async def test_remoteok_empty_query_keeps_all(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    fixture = [
        {"legal": "metadata"},
        {
            "id": "1",
            "position": "Python Developer",
            "company": "A",
            "location": "",
            "description": "",
            "url": "https://remoteok.com/jobs/1",
            "epoch": 1745000000,
            "tags": ["python"],
        },
        {
            "id": "2",
            "position": "UI Designer",
            "company": "B",
            "location": "",
            "description": "",
            "url": "https://remoteok.com/jobs/2",
            "epoch": 1745000000,
            "tags": ["design"],
        },
    ]

    with respx.mock:
        respx.get(REMOTEOK_BASE_URL).mock(
            return_value=httpx.Response(200, json=fixture)
        )
        jobs, _ = await source.search("", None, None, settings, MagicMock())

    assert len(jobs) == 2
