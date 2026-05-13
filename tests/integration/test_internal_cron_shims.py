import pytest
from httpx import ASGITransport, AsyncClient

CRON_SECRET = "dev-cron-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/internal/cron/process-sync-queue",
        "/internal/cron/process-match-queue",
        "/internal/cron/generation-queue",
    ],
)
async def test_deprecated_internal_cron_shims_return_202(db_session, path):
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(path, headers={"X-Cron-Secret": CRON_SECRET})

    assert response.status_code == 202
    assert response.json()["status"] == "deprecated"
