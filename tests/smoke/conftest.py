"""
Smoke test fixtures.

Tests run against any live HTTP endpoint — no in-process ASGI, no testcontainers.
Pass --base-url to target a specific environment (default: http://localhost:8000).
"""

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the running application under test",
    )


@pytest.fixture(scope="session")
def base_url(request):
    return request.config.getoption("--base-url").rstrip("/")


@pytest.fixture
async def client(base_url):
    """httpx client per test (avoids cross-test event loop conflicts)."""
    # 120s: covers slow operations (sync, LLM scoring/generation) and
    # DB lock waits when a background task from a previous run is still in-flight.
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as c:
        yield c
