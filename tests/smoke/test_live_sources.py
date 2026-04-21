"""Live API smoke tests for job source adapters.

These tests make real external API calls. They are opt-in only:
run with `-m live_api` to include them, otherwise they are skipped.
"""

import os
import uuid
from unittest.mock import MagicMock

import pytest

from app.sources.arbeitnow import ArbeitnowSource
from app.sources.remoteok import RemoteOKSource
from app.sources.remotive import RemotiveSource

pytestmark = pytest.mark.live_api


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.remotive_max_results = 50
    s.remoteok_user_agent = "job-application-agent/test"
    s.adzuna_cache_ttl_hours = 24
    return s


@pytest.mark.asyncio
async def test_remotive_returns_jobs():
    jobs, cursor = await RemotiveSource().search(
        "software engineer", None, None, _make_settings(), None
    )
    assert len(jobs) > 0
    assert cursor is None
    assert all(j.workplace_type == "remote" for j in jobs)
    assert all(j.apply_url for j in jobs)


@pytest.mark.asyncio
async def test_remoteok_returns_jobs():
    jobs, cursor = await RemoteOKSource().search(
        "software engineer", None, None, _make_settings(), None
    )
    assert len(jobs) > 0
    assert cursor is None
    assert all(j.workplace_type == "remote" for j in jobs)


@pytest.mark.asyncio
async def test_arbeitnow_returns_jobs_or_empty():
    result = await ArbeitnowSource().search(
        "software engineer", None, 1, _make_settings(), None
    )
    jobs, next_cursor = result
    assert isinstance(jobs, list)
    assert next_cursor is None or isinstance(next_cursor, int)


@pytest.mark.asyncio
async def test_greenhouse_board_valid_slug():
    slug = os.environ.get("TEST_GREENHOUSE_SLUG")
    if not slug:
        pytest.skip("TEST_GREENHOUSE_SLUG not set")

    from app.models.user_profile import UserProfile
    from app.sources.greenhouse_board import GreenhouseBoardSource

    profile = UserProfile(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        target_company_slugs={"greenhouse": [slug]},
        source_cursors={},
        search_active=False,
    )
    jobs, cursor = await GreenhouseBoardSource().search(
        "", None, None, _make_settings(), None, profile=profile
    )
    assert isinstance(jobs, list)
    assert cursor is None
