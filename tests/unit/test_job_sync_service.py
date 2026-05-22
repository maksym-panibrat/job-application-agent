from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

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

    def add(self, row):
        return None

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_sync_active_profiles_aggregates_shared_enqueue_contract():
    profiles = [
        SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000001")),
        SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000002")),
    ]
    session = _Session(profiles)

    stale_rows = [
        (profiles[0].id, "greenhouse", "airbnb"),
        (profiles[0].id, "greenhouse", "stripe"),
        (profiles[1].id, "greenhouse", "airbnb"),
    ]
    with (
        patch(
            "app.services.job_sync_service._prune_invalid_provider_slugs",
            new=AsyncMock(
                side_effect=[
                    ["greenhouse:deadcorp"],
                    [],
                ]
            ),
        ) as mock_prune,
        patch(
            "app.services.job_sync_service.slug_registry_service.list_stale_for_active_profiles",
            new=AsyncMock(return_value=stale_rows),
        ) as mock_list_stale,
        patch(
            "app.services.job_sync_service.enqueue",
            new=AsyncMock(return_value=123),
        ) as mock_enqueue,
    ):
        result = await job_sync_service.sync_active_profiles(session)

    assert result == {
        "enqueued": ["airbnb", "stripe"],
        "pruned": 1,
        "active_profiles": 2,
        "profiles_enqueued": 2,
    }
    assert mock_prune.await_count == 2
    mock_prune.assert_any_await(profiles[0], session)
    mock_prune.assert_any_await(profiles[1], session)
    mock_list_stale.assert_awaited_once_with(session, ttl_hours=6)
    assert mock_enqueue.await_count == 2
    assert [call.kwargs["payload"] for call in mock_enqueue.await_args_list] == [
        {"provider": "greenhouse", "slug": "airbnb"},
        {"provider": "greenhouse", "slug": "stripe"},
    ]
    assert [call.kwargs["dedupe_key"] for call in mock_enqueue.await_args_list] == [
        "fetch-slug:greenhouse:airbnb",
        "fetch-slug:greenhouse:stripe",
    ]
    assert profiles[0].last_sync_summary == {
        "queued_slugs": ["airbnb", "stripe"],
        "matched_now": 0,
        "pruned_slugs": ["greenhouse:deadcorp"],
    }
    assert profiles[1].last_sync_summary == {
        "queued_slugs": ["airbnb"],
        "matched_now": 0,
        "pruned_slugs": [],
    }
