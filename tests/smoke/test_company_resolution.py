"""
Smoke test for company resolution.

Hits the live server's POST /api/companies/resolve, which fans out to the
real Greenhouse public board API. The adapter HTTP layer is the only piece
not mocked — we accept the test depending on Greenhouse's public board
being reachable.

Self-cleaning: removes Stripe from target_company_ids on teardown so it
doesn't pollute the smoke profile across runs.
"""

import pytest


@pytest.mark.asyncio
async def test_resolve_stripe_real_greenhouse(client):
    """Resolve 'Stripe' end-to-end: POST resolve -> Company materializes -> PATCH
    profile -> GET profile surfaces Stripe under target_companies. Restores the
    profile's target_company_ids on exit."""
    original = (await client.get("/api/profile")).json()
    original_ids = [c["id"] for c in original.get("target_companies", [])]

    resp = await client.post("/api/companies/resolve", json={"name": "Stripe"})
    assert resp.status_code == 200, f"resolve failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["company"]["canonical_name"].lower() == "stripe"
    assert "greenhouse" in body["company"]["providers"]
    company_id = body["company"]["id"]

    new_ids = original_ids + [company_id]
    patch = await client.patch("/api/profile", json={"target_company_ids": new_ids})
    assert patch.status_code == 200

    profile = (await client.get("/api/profile")).json()
    names = [c["canonical_name"].lower() for c in profile["target_companies"]]
    assert "stripe" in names

    # Restore
    await client.patch("/api/profile", json={"target_company_ids": original_ids})
