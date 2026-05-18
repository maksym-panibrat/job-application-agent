from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import job_sync_service


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _ScalarResult(self._rows)


@pytest.mark.asyncio
async def test_sync_active_profiles_aggregates_shared_enqueue_contract():
    profiles = [SimpleNamespace(id="p1"), SimpleNamespace(id="p2")]
    session = _Session(profiles)

    with patch(
        "app.services.job_sync_service.prune_and_enqueue",
        new=AsyncMock(
            side_effect=[
                {
                    "queued_slugs": ["airbnb", "stripe"],
                    "matched_now": 0,
                    "pruned_slugs": ["greenhouse:deadcorp"],
                },
                {
                    "queued_slugs": [],
                    "matched_now": 0,
                    "pruned_slugs": [],
                },
            ]
        ),
    ) as mock_prune:
        result = await job_sync_service.sync_active_profiles(session)

    assert result == {
        "enqueued": ["airbnb", "stripe"],
        "pruned": 1,
        "active_profiles": 2,
        "profiles_enqueued": 1,
    }
    assert mock_prune.await_count == 2
    mock_prune.assert_any_await(profiles[0], session)
    mock_prune.assert_any_await(profiles[1], session)
