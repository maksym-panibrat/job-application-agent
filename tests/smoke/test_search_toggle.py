"""
Smoke test for search toggle — self-restoring, prod-safe.
"""

import pytest


@pytest.mark.asyncio
async def test_toggle_search(client):
    # Pause
    resp = await client.patch("/api/profile/search", json={"search_active": False})
    assert resp.status_code == 200
    assert resp.json()["search_active"] is False

    # Resume — restores original state
    resp = await client.patch("/api/profile/search", json={"search_active": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["search_active"] is True
    assert data["search_expires_at"] is not None
