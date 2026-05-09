"""Integration tests for POST /api/companies/resolve."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.company import Company


@pytest.mark.asyncio
async def test_resolve_success(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    fake = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear"},
        resolved_at=datetime.now(UTC),
    )
    with patch(
        "app.api.companies.company_resolver.resolve",
        new=AsyncMock(return_value=fake),
    ):
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
    assert set(body["company"]["providers"]) == {"ashby"}
    assert "id" in body["company"]


@pytest.mark.asyncio
async def test_resolve_not_found(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    with patch(
        "app.api.companies.company_resolver.resolve",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/companies/resolve",
                json={"name": "nope-co"},
                headers=auth_headers,
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_timeout_returns_503(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app
    from app.services.company_resolver import FanoutTimeoutError

    with patch(
        "app.api.companies.company_resolver.resolve",
        new=AsyncMock(side_effect=FanoutTimeoutError("linear")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/companies/resolve",
                json={"name": "Linear"},
                headers=auth_headers,
            )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_resolve_empty_name_returns_validation_error(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": ""},
            headers=auth_headers,
        )
    # Pydantic min_length=1 -> 422 (validation error). The handler also has
    # a redundant guard that returns 400 if a whitespace-only string slips
    # past Pydantic; either is acceptable.
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_resolve_whitespace_only_returns_400(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": "   "},  # passes min_length, fails the strip() guard
            headers=auth_headers,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_unauthenticated_returns_401(db_session):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/companies/resolve",
            json={"name": "Linear"},
        )
    assert resp.status_code in (401, 403)
