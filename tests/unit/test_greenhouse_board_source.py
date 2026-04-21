"""Unit tests for the GreenhouseBoardSource adapter."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE, GreenhouseBoardSource

STRIPE_JOB_FIXTURE = {
    "jobs": [
        {
            "id": 4000001,
            "title": "Software Engineer",
            "location": {"name": "San Francisco, CA"},
            "content": "<div>Great opportunity</div>",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4000001",
            "updated_at": "2026-04-01T10:00:00Z",
        }
    ]
}

AIRBNB_JOB_FIXTURE = {
    "jobs": [
        {
            "id": 4000002,
            "title": "Staff Engineer",
            "location": {"name": "New York, NY"},
            "content": "<div>Scale with us</div>",
            "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/4000002",
            "updated_at": "2026-04-02T10:00:00Z",
        }
    ]
}


def make_settings() -> MagicMock:
    settings = MagicMock()
    settings.adzuna_cache_ttl_hours = 24
    return settings


def no_cache_source(monkeypatch) -> GreenhouseBoardSource:
    source = GreenhouseBoardSource()
    monkeypatch.setattr(source, "_get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr(source, "_save_cache", AsyncMock(return_value=None))
    return source


def make_profile(slugs: dict) -> MagicMock:
    profile = MagicMock()
    profile.target_company_slugs = slugs
    return profile


async def do_search(source, profile):
    return await source.search("", None, None, make_settings(), MagicMock(), profile=profile)


@pytest.mark.asyncio
async def test_greenhouse_board_happy_path(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({"greenhouse": ["stripe"]})

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        jobs, cursor = await do_search(source, profile)

    assert cursor is None
    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "4000001"
    assert job.company_name == "Stripe"
    assert job.ats_type == "greenhouse"
    assert job.supports_api_apply is True
    assert job.location == "San Francisco, CA"
    assert job.workplace_type is None


@pytest.mark.asyncio
async def test_greenhouse_board_two_slugs_combined(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({"greenhouse": ["stripe", "airbnb"]})

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb/jobs").mock(
            return_value=httpx.Response(200, json=AIRBNB_JOB_FIXTURE)
        )
        jobs, cursor = await do_search(source, profile)

    assert cursor is None
    assert len(jobs) == 2
    company_names = {j.company_name for j in jobs}
    assert company_names == {"Stripe", "Airbnb"}


@pytest.mark.asyncio
async def test_greenhouse_board_404_skips_slug(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({"greenhouse": ["bad-slug", "stripe"]})

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(return_value=httpx.Response(404))
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        jobs, cursor = await do_search(source, profile)

    assert cursor is None
    assert len(jobs) == 1
    assert jobs[0].external_id == "4000001"


@pytest.mark.asyncio
async def test_greenhouse_board_no_profile(monkeypatch):
    source = no_cache_source(monkeypatch)

    jobs, cursor = await do_search(source, None)

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_greenhouse_board_no_greenhouse_slugs(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({})

    jobs, cursor = await do_search(source, profile)

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_greenhouse_board_remote_location(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({"greenhouse": ["stripe"]})

    fixture = {
        "jobs": [
            {
                "id": 4000003,
                "title": "Remote Engineer",
                "location": {"name": "Remote"},
                "content": "<div>Work from anywhere</div>",
                "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4000003",
                "updated_at": "2026-04-01T10:00:00Z",
            }
        ]
    }

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        jobs, cursor = await do_search(source, profile)

    assert len(jobs) == 1
    assert jobs[0].workplace_type == "remote"


@pytest.mark.asyncio
async def test_greenhouse_board_other_error_skips_slug(monkeypatch):
    source = no_cache_source(monkeypatch)
    profile = make_profile({"greenhouse": ["bad-slug", "stripe"]})

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        jobs, cursor = await do_search(source, profile)

    assert cursor is None
    assert len(jobs) == 1
    assert jobs[0].external_id == "4000001"
