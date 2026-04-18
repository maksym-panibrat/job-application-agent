"""
Smoke test fixtures.

Tests run against any live HTTP endpoint — no in-process ASGI, no testcontainers.
Pass --base-url to target a specific environment (default: http://localhost:8000).
Pass --has-seed-api when the target has dev-mode /api/test/seed endpoints.
"""

import asyncio
import time

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the running application under test",
    )
    parser.addoption(
        "--has-seed-api",
        action="store_true",
        default=False,
        help="Target exposes /api/test/seed (dev mode only — do NOT use against production)",
    )


@pytest.fixture(scope="session")
def base_url(request):
    return request.config.getoption("--base-url").rstrip("/")


@pytest.fixture(scope="session")
def has_seed_api(request):
    return request.config.getoption("--has-seed-api")


@pytest.fixture
async def client(base_url):
    """httpx client per test (avoids cross-test event loop conflicts)."""
    # 120s: covers slow operations (sync, LLM scoring/generation) and
    # DB lock waits when a background task from a previous run is still in-flight.
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as c:
        yield c


@pytest.fixture
def require_seed_api(has_seed_api):
    """Depend on this fixture to skip a test unless --has-seed-api was passed."""
    if not has_seed_api:
        pytest.skip("requires --has-seed-api (dev environment only)")


@pytest.fixture
async def seeded_data(client, require_seed_api):
    """
    POST /api/test/seed before the test, DELETE /api/test/seed after.
    Yields the seed response: {"jobs": [...], "applications": [...]}.
    """
    resp = await client.post("/api/test/seed")
    assert resp.status_code == 200, f"Seed failed: {resp.text}"
    data = resp.json()
    yield data
    await client.delete("/api/test/seed")


async def poll_until(client, url, predicate, *, timeout=60, interval=2):
    """
    Poll GET `url` until `predicate(response_json)` returns True or `timeout` seconds pass.
    Useful for asserting on results of background tasks.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(url)
        if resp.status_code == 200 and predicate(resp.json()):
            return resp.json()
        await asyncio.sleep(interval)
    pytest.fail(f"Timed out after {timeout}s polling {url}")
