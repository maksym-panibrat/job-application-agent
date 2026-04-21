"""Unit tests for the Arbeitnow job source adapter."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.sources.arbeitnow import ARBEITNOW_BASE_URL, ArbeitnowSource

FIXTURE_JOB = {
    "slug": "backend-engineer-acme-123",
    "title": "Backend Engineer",
    "company_name": "Acme",
    "location": "Berlin, Germany",
    "remote": False,
    "description": "<p>Join us!</p>",
    "url": "https://www.arbeitnow.com/jobs/backend-engineer-acme-123",
    "created_at": 1745000000,
    "job_types": ["full_time"],
}

FIXTURE_RESPONSE = {"data": [FIXTURE_JOB]}


def make_settings() -> MagicMock:
    settings = MagicMock()
    settings.adzuna_cache_ttl_hours = 24
    return settings


def no_cache_source(monkeypatch) -> ArbeitnowSource:
    source = ArbeitnowSource()
    monkeypatch.setattr(source, "_get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(source, "_save_cache", AsyncMock(return_value=None))
    return source


@pytest.mark.asyncio
async def test_arbeitnow_happy_path(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(return_value=httpx.Response(200, json=FIXTURE_RESPONSE))
        jobs, cursor = await source.search("backend", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert cursor == 2

    job = jobs[0]
    assert job.external_id == "backend-engineer-acme-123"
    assert job.title == "Backend Engineer"
    assert job.company_name == "Acme"
    assert job.location == "Berlin, Germany"
    assert job.workplace_type is None
    assert job.contract_type == "full_time"
    assert job.posted_at == datetime.fromtimestamp(1745000000, tz=UTC)
    assert job.posted_at.tzinfo is not None


@pytest.mark.asyncio
async def test_arbeitnow_empty_page_stops_pagination(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        jobs, cursor = await source.search("backend", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_arbeitnow_query_filter(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    response_data = {
        "data": [
            {**FIXTURE_JOB, "slug": "be-1", "title": "Backend Engineer"},
            {**FIXTURE_JOB, "slug": "ui-1", "title": "UI Designer"},
        ]
    }

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(return_value=httpx.Response(200, json=response_data))
        jobs, _ = await source.search("backend", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert jobs[0].external_id == "be-1"


@pytest.mark.asyncio
async def test_arbeitnow_empty_query_keeps_all(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    response_data = {
        "data": [
            {**FIXTURE_JOB, "slug": "be-1", "title": "Backend Engineer"},
            {**FIXTURE_JOB, "slug": "ui-1", "title": "UI Designer"},
        ]
    }

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(return_value=httpx.Response(200, json=response_data))
        jobs, _ = await source.search("", None, None, settings, MagicMock())

    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_arbeitnow_remote_true(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    remote_job = {**FIXTURE_JOB, "remote": True}

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(
            return_value=httpx.Response(200, json={"data": [remote_job]})
        )
        jobs, _ = await source.search("backend", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert jobs[0].workplace_type == "remote"


@pytest.mark.asyncio
async def test_arbeitnow_http_error(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(return_value=httpx.Response(500))
        jobs, cursor = await source.search("backend", None, None, settings, MagicMock())

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_arbeitnow_job_types_joined(monkeypatch):
    source = no_cache_source(monkeypatch)
    settings = make_settings()

    multi_type_job = {**FIXTURE_JOB, "job_types": ["full_time", "contract"]}

    with respx.mock:
        respx.get(ARBEITNOW_BASE_URL).mock(
            return_value=httpx.Response(200, json={"data": [multi_type_job]})
        )
        jobs, _ = await source.search("backend", None, None, settings, MagicMock())

    assert len(jobs) == 1
    assert jobs[0].contract_type == "full_time / contract"
