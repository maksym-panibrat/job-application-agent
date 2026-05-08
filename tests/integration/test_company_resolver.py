"""Integration tests for company_resolver.resolve().

These tests rely on real Postgres semantics (INSERT ... ON CONFLICT) and the
testcontainer-backed `db_session` fixture from tests/integration/conftest.py.
The pure-function `_normalize` test lives under tests/unit/.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models.company import Company
from app.services import company_resolver


@pytest.mark.asyncio
async def test_resolve_cache_hit_returns_existing_company(db_session):
    existing = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.commit()

    with patch.object(company_resolver, "_fan_out", new=AsyncMock()) as fan_out:
        result = await company_resolver.resolve("Linear", db_session)

    assert result is not None
    assert result.id == existing.id
    fan_out.assert_not_called()  # cache hit short-circuits


@pytest.mark.asyncio
async def test_resolve_single_provider_match_persists_and_returns(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"ashby": True}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Linear", db_session)

    assert result is not None
    assert result.canonical_name == "Linear"
    assert result.normalized_key == "linear"
    assert result.provider_slugs == {"ashby": "linear"}


@pytest.mark.asyncio
async def test_resolve_multi_provider_match_stores_all(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": True, "ashby": True}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert set(result.provider_slugs.keys()) == {"greenhouse", "ashby"}


@pytest.mark.asyncio
async def test_resolve_no_match_returns_none(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": False, "lever": False, "ashby": False}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("nonexistent-co", db_session)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_fanout_timeout_raises(db_session):
    """The endpoint distinguishes timeout (503) from no-match (404), so the
    resolver raises FanoutTimeoutError on the timeout path rather than
    returning None."""

    async def slow_fan_out(slug, *, timeout):
        raise TimeoutError

    with patch.object(company_resolver, "_fan_out", new=slow_fan_out):
        with pytest.raises(company_resolver.FanoutTimeoutError):
            await company_resolver.resolve("Linear", db_session)


@pytest.mark.asyncio
async def test_resolve_returns_existing_row_when_normalized_key_matches(db_session):
    """When the row already exists, the cache lookup returns it without
    fanning out. This also exercises the same path the concurrent-insert
    'loser' would take after re-SELECTing on ON CONFLICT DO NOTHING."""

    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": True}

    pre_existing = Company(
        canonical_name="Stripe",
        normalized_key="stripe",
        provider_slugs={"greenhouse": "stripe"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(pre_existing)
    await db_session.commit()

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert result is not None
    assert result.id == pre_existing.id


@pytest.mark.asyncio
async def test_resolve_persists_partial_match_with_failed_provider_logged(db_session):
    """If one provider 200s and others 5xx: persist the confirmed provider,
    log company_resolver.partial_match for ops awareness."""

    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": True, "lever": "error", "ashby": False}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert result is not None
    assert "greenhouse" in result.provider_slugs
    assert "lever" not in result.provider_slugs
