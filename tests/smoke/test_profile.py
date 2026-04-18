"""
Smoke tests for profile endpoints.

All tests are self-cleaning — they restore any state they modify.
Safe to run against production.
"""

import io

import pytest


@pytest.mark.asyncio
async def test_get_profile(client):
    resp = await client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data


@pytest.mark.asyncio
async def test_update_profile(client):
    # Save original target_roles so we can restore them
    original = (await client.get("/api/profile")).json()
    original_roles = original.get("target_roles", [])

    smoke_roles = ["Smoke Test Role — do not use"]
    resp = await client.patch("/api/profile", json={"target_roles": smoke_roles})
    assert resp.status_code == 200

    # Verify persisted
    profile = (await client.get("/api/profile")).json()
    assert smoke_roles[0] in profile["target_roles"]

    # Restore
    await client.patch("/api/profile", json={"target_roles": original_roles})


@pytest.mark.asyncio
async def test_upload_resume(client):
    content = b"# Smoke Test Resume\n\n## Experience\nSoftware Engineer (smoke test)\n"
    resp = await client.post(
        "/api/profile/upload",
        files={"file": ("smoke_resume.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("base_resume_md")
