"""Integration tests for the onboarding agent's list_curated_companies tool."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agents.onboarding import list_curated_companies
from app.models.company import Company
from app.services.company_catalog import seed_catalog

FIXTURES = Path(__file__).parent / "_catalog_fixtures"


@pytest.mark.asyncio
async def test_list_curated_companies_returns_curated_rows_with_tags(
    db_session, asyncpg_url, monkeypatch
):
    """Calling the tool returns curated rows + tags as JSON, alphabetical,
    excluding organic-resolution rows."""
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)
    from app.database import get_session_factory

    # Seed two curated rows with tags.
    await seed_catalog(db_session, source=FIXTURES / "two_rows_with_tags.yaml")

    # Add an organic row that should NOT appear.
    organic = Company(
        canonical_name="OrganicCo",
        normalized_key="organicco",
        provider_slugs={"greenhouse": "organicco"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(organic)
    await db_session.commit()

    db_factory = get_session_factory()
    result = await list_curated_companies.ainvoke(
        {},  # no tool args
        config={"configurable": {"db_factory": db_factory}},
    )

    payload = json.loads(result)
    assert isinstance(payload, list)
    names = {row["canonical_name"] for row in payload}
    assert "TestStripe" in names
    assert "TestLinear" in names
    assert "OrganicCo" not in names

    # Tags surface intact.
    by_name = {row["canonical_name"]: row for row in payload}
    assert set(by_name["TestStripe"]["tags"]) == {"fintech", "infra"}
    assert set(by_name["TestLinear"]["tags"]) == {"dev-tools", "b2b"}

    # Alphabetical (case-insensitive).
    ordered_names = [row["canonical_name"] for row in payload]
    assert ordered_names == sorted(ordered_names, key=lambda s: s.lower())


@pytest.mark.asyncio
async def test_list_curated_companies_response_shape(db_session, asyncpg_url, monkeypatch):
    """Each row has exactly the keys {canonical_name, tags}; no id leaks."""
    monkeypatch.setenv("DATABASE_URL", asyncpg_url)
    from app.database import get_session_factory

    await seed_catalog(db_session, source=FIXTURES / "two_rows_with_tags.yaml")

    db_factory = get_session_factory()
    result = await list_curated_companies.ainvoke(
        {},
        config={"configurable": {"db_factory": db_factory}},
    )
    payload = json.loads(result)
    assert isinstance(payload, list)
    for row in payload:
        assert set(row.keys()) == {"canonical_name", "tags"}
        assert isinstance(row["canonical_name"], str)
        assert isinstance(row["tags"], list)
        assert all(isinstance(t, str) for t in row["tags"])
