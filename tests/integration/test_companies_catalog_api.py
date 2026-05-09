"""Integration tests for GET /api/companies/catalog."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.company import Company
from app.services.company_catalog import seed_catalog

FIXTURES = Path(__file__).parent / "_catalog_fixtures"


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_only_curated_rows(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    # Seed two curated rows.
    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    # Add an organic row that should NOT appear.
    organic = Company(
        canonical_name="OrganicCo",
        normalized_key="organicco",
        provider_slugs={"greenhouse": "organicco"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(organic)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    names = {row["canonical_name"] for row in body}
    assert "TestStripe" in names
    assert "TestLinear" in names
    assert "OrganicCo" not in names


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_alphabetical_order(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    body = resp.json()
    names = [row["canonical_name"] for row in body]
    # Case-insensitive alphabetical: TestLinear < TestStripe.
    assert names == sorted(names, key=lambda s: s.lower())


@pytest.mark.asyncio
async def test_catalog_endpoint_response_shape(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    body = resp.json()
    assert isinstance(body, list)
    for row in body:
        assert set(row.keys()) == {"id", "canonical_name"}
        assert isinstance(row["id"], str)
        assert isinstance(row["canonical_name"], str)


@pytest.mark.asyncio
async def test_catalog_endpoint_requires_auth(db_session):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog")
    assert resp.status_code in (401, 403)
