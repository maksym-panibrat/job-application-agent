import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_profile_api_deprecates_search_keywords(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    _, profile = seeded_user
    profile.search_keywords = ["legacy-python"]
    db_session.add(profile)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        read_before = await client.get("/api/profile", headers=auth_headers)
        assert read_before.status_code == 200
        assert "search_keywords" not in read_before.json()

        patch = await client.patch(
            "/api/profile",
            json={"search_keywords": ["new-keyword"]},
            headers=auth_headers,
        )
        assert patch.status_code == 200

    await db_session.refresh(profile)
    assert profile.search_keywords == ["legacy-python"]
