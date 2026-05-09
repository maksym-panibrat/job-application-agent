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
        jobs = await source.fetch_jobs("stripe")

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
        stripe_jobs = await source.fetch_jobs("stripe")
        airbnb_jobs = await source.fetch_jobs("airbnb")

    company_names = {j.company_name for j in stripe_jobs + airbnb_jobs}
    assert company_names == {"Stripe", "Airbnb"}


@pytest.mark.asyncio
async def test_greenhouse_board_404_raises_invalid_slug():
    """404 from Greenhouse means the slug doesn't exist — surface this so the
    caller can warn the user (issue #47). Previously silently returned []."""
    from app.sources.base import InvalidSlugError

    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(return_value=httpx.Response(404))
        with pytest.raises(InvalidSlugError):
            await source.fetch_jobs("bad-slug")


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
        jobs = await source.fetch_jobs("stripe")

    assert len(jobs) == 1
    assert jobs[0].workplace_type == "remote"


@pytest.mark.asyncio
async def test_greenhouse_board_connection_error_raises_transient():
    """Network errors are transient — surface so the caller can retry next sync (#47)."""
    from app.sources.base import TransientFetchError

    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/bad-slug/jobs").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with pytest.raises(TransientFetchError):
            await source.fetch_jobs("bad-slug")


@pytest.mark.asyncio
async def test_greenhouse_board_passes_through_raw_html_content():
    """Issue #51 + Task A1: the adapter no longer markdownifies — it stores the
    upstream HTML untouched in description_raw, and job_service runs the
    cleaner pipeline once. Asserts no double-clean: the raw HTML survives."""
    source = GreenhouseBoardSource()

    raw_html = (
        "<h1>About the role</h1>"
        "<p>You will <strong>scale</strong> our systems.</p>"
        "<ul><li>Python</li><li>Postgres</li></ul>"
    )
    fixture = {
        "jobs": [
            {
                "id": 4000010,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "content": raw_html,
                "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4000010",
                "updated_at": "2026-04-01T10:00:00Z",
            }
        ]
    }

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        jobs = await source.fetch_jobs("stripe")

    assert len(jobs) == 1
    assert jobs[0].description_raw == raw_html


@pytest.mark.asyncio
async def test_greenhouse_board_5xx_raises_transient():
    """5xx from Greenhouse is transient — distinguish from 404 (#47)."""
    from app.sources.base import TransientFetchError

    source = GreenhouseBoardSource()

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
            return_value=httpx.Response(503, text="service unavailable")
        )
        with pytest.raises(TransientFetchError):
            await source.fetch_jobs("stripe")


@pytest.mark.asyncio
async def test_validate_slug_returns_true_on_200():
    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb").mock(
            return_value=httpx.Response(200, json={"name": "Airbnb", "content": "<p/>"})
        )
        assert await source.validate("airbnb") is True


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404():
    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        assert await source.validate("openai") is False


@pytest.mark.asyncio
async def test_validate_5xx_raises_transient():
    """5xx upstream is a transient error, not a confirmed miss. _fan_out
    distinguishes 'error' from False, so the next sync cycle's SlugFetch
    retry can repair the gap if the validate happened during a blip."""
    from app.sources.base import TransientFetchError

    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/flaky").mock(return_value=httpx.Response(503))
        with pytest.raises(TransientFetchError):
            await source.validate("flaky")


@pytest.mark.asyncio
async def test_validate_network_error_raises_transient():
    """A connection-level failure is a transient error, not a confirmed miss."""
    from app.sources.base import TransientFetchError

    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/blip").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with pytest.raises(TransientFetchError):
            await source.validate("blip")


@pytest.mark.asyncio
async def test_uses_shared_client_when_provided():
    """When called with a shared client, no per-call client is created."""
    source = GreenhouseBoardSource()
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
                return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
            )
            jobs = await source.fetch_jobs("stripe", client=client)
    assert len(jobs) == 1
