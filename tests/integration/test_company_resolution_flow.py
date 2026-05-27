"""End-to-end resolution flow with the httpx layer mocked.

Stricter than test_companies_api (which mocks company_resolver) and
test_company_resolver (which mocks _fan_out). Here only the upstream ATS
HTTP is mocked; the resolver, the SOURCES adapters, the API endpoint,
and the Company persistence all run for real."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlmodel import select

from app.models.company import Company
from app.models.subscription import Subscription, SubscriptionAccount, SubscriptionPlan


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


async def _seed_paid_subscription(db_session, user_id: uuid.UUID) -> Subscription:
    paid_plan = (
        await db_session.execute(select(SubscriptionPlan).where(SubscriptionPlan.tier == "paid"))
    ).scalar_one_or_none()
    if paid_plan is None:
        paid_plan = SubscriptionPlan(
            tier="paid",
            display_name="Paid",
            followed_company_limit=100,
        )
        db_session.add(paid_plan)
        await db_session.commit()
        await db_session.refresh(paid_plan)

    account = SubscriptionAccount(
        user_id=user_id,
        provider="test",
        provider_customer_id=f"cus_{uuid.uuid4()}",
    )
    db_session.add(account)
    await db_session.flush()

    now = datetime.now(UTC)
    subscription = Subscription(
        user_id=user_id,
        subscription_account_id=account.id,
        plan_id=paid_plan.id,
        provider="test",
        provider_subscription_id=f"sub_{uuid.uuid4()}",
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(subscription)
    return subscription


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
async def test_get_profile_free_user_includes_canonical_subscription_entitlements_and_limits(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"] is None
    assert body["entitlements"] == {"paid_access": False, "search_auto_pause": True}
    assert body["limits"]["followed_companies"] == 5


@pytest.mark.asyncio
async def test_get_profile_paid_user_includes_canonical_subscription_entitlements_and_limits(
    db_session, auth_headers, seeded_user
):
    from app.main import app as fastapi_app

    user, _ = seeded_user
    await _seed_paid_subscription(db_session, user.id)

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/profile", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["subscription"]["tier"] == "paid"
    assert body["subscription"]["status"] == "active"
    assert body["subscription"]["current_period_end"] is not None
    assert body["entitlements"] == {"paid_access": True, "search_auto_pause": False}
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
    await _seed_paid_subscription(db_session, user.id)
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
    expected_ids = [company.id for company in companies]
    db_session.expire_all()
    await db_session.refresh(profile)
    assert profile.target_company_ids == expected_ids


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

    subset_ids = [company.id for company in companies[:6]]
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/profile",
            json={"target_company_ids": [str(company_id) for company_id in subset_ids]},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    db_session.expire_all()
    await db_session.refresh(profile)
    assert profile.target_company_ids == subset_ids
