import httpx
import pytest

from scripts.smoke.golden_path import step6_cron_sync


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
                "status": "ok",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok, details = await step6_cron_sync(
            client,
            "https://job-search.example",
            "secret",
            verbose=False,
        )

    assert ok is True
    assert details["step"] == 6
    assert details["status"] == "ok"
