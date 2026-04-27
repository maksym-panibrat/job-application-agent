"""Unit tests for the GreenhouseBoardSource adapter."""

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


@pytest.mark.asyncio
async def test_greenhouse_board_happy_path():
    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        jobs, cursor = await source.search("", None, slug="stripe")

    assert cursor is None
    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "4000001"
    assert job.company_name == "Stripe"
    assert job.location == "San Francisco, CA"
    assert job.workplace_type is None


@pytest.mark.asyncio
async def test_greenhouse_board_two_slugs_called_independently():
    """The source takes one slug per call; service layer iterates."""
    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
        )
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb/jobs").mock(
            return_value=httpx.Response(200, json=AIRBNB_JOB_FIXTURE)
        )
        stripe_jobs, _ = await source.search("", None, slug="stripe")
        airbnb_jobs, _ = await source.search("", None, slug="airbnb")

    company_names = {j.company_name for j in stripe_jobs + airbnb_jobs}
    assert company_names == {"Stripe", "Airbnb"}


@pytest.mark.asyncio
async def test_greenhouse_board_404_returns_empty():
    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(return_value=httpx.Response(404))
        jobs, cursor = await source.search("", None, slug="bad-slug")

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_greenhouse_board_no_slug_returns_empty():
    """A None slug short-circuits to an empty result set."""
    source = GreenhouseBoardSource()

    jobs, cursor = await source.search("", None, slug=None)

    assert jobs == []
    assert cursor is None


@pytest.mark.asyncio
async def test_greenhouse_board_remote_location():
    source = GreenhouseBoardSource()

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
        jobs, _ = await source.search("", None, slug="stripe")

    assert len(jobs) == 1
    assert jobs[0].workplace_type == "remote"


@pytest.mark.asyncio
async def test_greenhouse_board_connection_error_returns_empty():
    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        jobs, cursor = await source.search("", None, slug="bad-slug")

    assert jobs == []
    assert cursor is None
