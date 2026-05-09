"""End-to-end resolution flow with the httpx layer mocked.

Stricter than test_companies_api (which mocks company_resolver) and
test_company_resolver (which mocks _fan_out). Here only the upstream ATS
HTTP is mocked; the resolver, the SOURCES adapters, the API endpoint,
and the Company persistence all run for real."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_then_get_profile_surfaces_company(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    # Mock all three providers: greenhouse 200, lever 404, ashby 200.
    respx.get("https://boards-api.greenhouse.io/v1/boards/linear").mock(return_value=Response(200))
    respx.get("https://api.lever.co/v0/postings/linear").mock(return_value=Response(404))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").mock(
        return_value=Response(200, json={"jobs": []})
    )

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": "Linear"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["company"]["canonical_name"] == "Linear"
        assert set(body["company"]["providers"]) == {"greenhouse", "ashby"}
        company_id = body["company"]["id"]

        # PATCH profile with the new id
        profile_resp = await client.get("/api/profile", headers=auth_headers)
        current_ids = [c["id"] for c in profile_resp.json().get("target_companies", [])]
        new_ids = current_ids + [company_id]
        patch_resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": new_ids},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200

        # GET surfaces it
        profile_after = await client.get("/api/profile", headers=auth_headers)
        names = [c["canonical_name"] for c in profile_after.json()["target_companies"]]
        assert "Linear" in names


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_no_match_returns_404(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    respx.get("https://boards-api.greenhouse.io/v1/boards/nope-co").mock(return_value=Response(404))
    respx.get("https://api.lever.co/v0/postings/nope-co").mock(return_value=Response(404))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/nope-co").mock(
        return_value=Response(404)
    )

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": "nope-co"},
            headers=auth_headers,
        )
    assert resp.status_code == 404


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_multi_provider_match_persists_all(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    respx.get("https://boards-api.greenhouse.io/v1/boards/migrating-co").mock(
        return_value=Response(200)
    )
    respx.get("https://api.lever.co/v0/postings/migrating-co").mock(return_value=Response(404))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/migrating-co").mock(
        return_value=Response(200, json={"jobs": []})
    )

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": "migrating-co"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert set(resp.json()["company"]["providers"]) == {"greenhouse", "ashby"}
