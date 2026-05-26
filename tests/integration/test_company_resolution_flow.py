"""End-to-end resolution flow with the httpx layer mocked.

Stricter than test_companies_api (which mocks company_resolver) and
test_company_resolver (which mocks _fan_out). Here only the upstream ATS
HTTP is mocked; the resolver, the SOURCES adapters, the API endpoint,
and the Company persistence all run for real."""

import uuid

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.models.company import Company


async def _seed_companies(db_session, count: int) -> list[Company]:
    companies = [
        Company(
            canonical_name=f"Limit Test {uuid.uuid4()}",
            normalized_key=f"limit-test-{uuid.uuid4()}",
            provider_slugs={"greenhouse": f"limit-test-{uuid.uuid4()}"},
        )
        for _ in range(count)
    ]
    db_session.add_all(companies)
    await db_session.commit()
    for company in companies:
        await db_session.refresh(company)
    return companies


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


@pytest.mark.asyncio
async def test_get_profile_includes_subscription_and_limits(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    user, _ = seeded_user
    user.subscription_plan = "paid"
    user.subscription_status = "active"
    db_session.add(user)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"]["plan"] == "paid"
    assert body["subscription"]["status"] == "active"
    assert body["subscription"]["paid_active"] is True
    assert body["limits"]["followed_companies"] == 100


@pytest.mark.asyncio
async def test_patch_profile_rejects_six_free_companies(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    companies = await _seed_companies(db_session, 6)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(company.id) for company in companies]},
            headers=auth_headers,
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "Free accounts can follow up to 5 companies."


@pytest.mark.asyncio
async def test_patch_profile_paid_active_user_can_save_six_companies(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    user, profile = seeded_user
    user.subscription_plan = "paid"
    user.subscription_status = "active"
    db_session.add(user)
    await db_session.commit()
    companies = await _seed_companies(db_session, 6)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(company.id) for company in companies]},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    db_session.expire_all()
    await db_session.refresh(profile)
    assert profile.target_company_ids == [company.id for company in companies]


@pytest.mark.asyncio
async def test_patch_profile_company_limit_failure_does_not_persist_other_fields(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    companies = await _seed_companies(db_session, 6)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={
                "full_name": "Should Not Persist",
                "target_company_ids": [str(company.id) for company in companies],
            },
            headers=auth_headers,
        )
        profile_resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 422
    assert profile_resp.json()["full_name"] != "Should Not Persist"


@pytest.mark.asyncio
async def test_patch_profile_downgraded_over_limit_user_can_save_removal_only_subset(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    _, profile = seeded_user
    companies = await _seed_companies(db_session, 8)
    profile.target_company_ids = [company.id for company in companies]
    db_session.add(profile)
    await db_session.commit()

    subset = companies[:6]
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(company.id) for company in subset]},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    db_session.expire_all()
    await db_session.refresh(profile)
    assert profile.target_company_ids == [company.id for company in subset]
