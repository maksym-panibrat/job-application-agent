import httpx
import pytest

from scripts.smoke.golden_path import _step_cron_sync


@pytest.mark.asyncio
async def test_cron_sync_accepts_202_accepted():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Cron-Secret"] == "secret"
        return httpx.Response(
            202,
            json={
                "enqueued": [1, 2],
                "pruned": 0,
                "active_profiles": 1,
                "profiles_enqueued": 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        details = await _step_cron_sync(client, "https://job-search.example", "secret")

    assert details["enqueued"] == [1, 2]
    assert details["active_profiles"] == 1
