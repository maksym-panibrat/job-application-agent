"""Integration tests for company_catalog.seed_catalog."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import select

from app.models.company import Company
from app.services.company_catalog import seed_catalog

FIXTURES = Path(__file__).parent / "_catalog_fixtures"


@pytest.mark.asyncio
async def test_seed_catalog_inserts_yaml_rows_as_curated(db_session):
    """First boot on an empty DB: every YAML row materializes as is_curated=true."""
    yaml_path = FIXTURES / "two_rows.yaml"
    count = await seed_catalog(db_session, source=yaml_path)
    assert count == 2

    rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(rows) == 2
    by_key = {r.normalized_key: r for r in rows}
    assert by_key["teststripe"].canonical_name == "TestStripe"
    assert by_key["teststripe"].provider_slugs == {"greenhouse": "teststripe"}
    assert by_key["teststripe"].is_curated is True
    assert by_key["testlinear"].canonical_name == "TestLinear"
    assert by_key["testlinear"].provider_slugs == {"ashby": "testlinear"}
    assert by_key["testlinear"].is_curated is True


@pytest.mark.asyncio
async def test_seed_catalog_is_idempotent(db_session):
    """Running the seed twice produces the same DB state — no duplicate rows,
    no flag flapping, and the Company id is stable across runs."""
    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    first_ids = {
        r.normalized_key: r.id
        for r in (
            await db_session.execute(
                select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
            )
        ).scalars().all()
    }

    await seed_catalog(db_session, source=yaml_path)

    second_rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(second_rows) == 2
    second_ids = {r.normalized_key: r.id for r in second_rows}
    assert first_ids == second_ids
    assert all(r.is_curated for r in second_rows)


@pytest.mark.asyncio
async def test_seed_catalog_promotes_organic_company_to_curated(db_session):
    """A Company resolved organically via Layer 1 fan-out exists with
    is_curated=false. When the catalog adds a row that matches by
    normalized_key, the existing Company is upgraded — same id, is_curated
    flipped to true, provider_slugs refreshed from the YAML."""
    organic = Company(
        canonical_name="teststripe",  # lowercase casing from the user's input
        normalized_key="teststripe",
        provider_slugs={"greenhouse": "old-slug"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(organic)
    await db_session.commit()
    await db_session.refresh(organic)
    organic_id = organic.id
    assert organic.is_curated is False

    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    refreshed = (
        await db_session.execute(
            select(Company).where(Company.normalized_key == "teststripe")
        )
    ).scalar_one()
    assert refreshed.id == organic_id  # stable across promotion
    assert refreshed.is_curated is True
    assert refreshed.canonical_name == "TestStripe"  # YAML casing wins
    assert refreshed.provider_slugs == {"greenhouse": "teststripe"}  # YAML slugs win


@pytest.mark.asyncio
async def test_seed_catalog_drops_curated_flag_when_row_removed_from_yaml(db_session):
    """A row dropped from the YAML on a later deploy → is_curated flips to
    false on the next seed. The Company row stays in the DB; following users
    are unaffected."""
    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    # Now seed against an empty catalog. The two rows from the previous run
    # should lose is_curated but stay in the DB.
    await seed_catalog(db_session, source=FIXTURES / "empty.yaml")

    rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(rows) == 2  # not deleted
    assert all(r.is_curated is False for r in rows)


@pytest.mark.asyncio
async def test_seed_catalog_raises_on_malformed_file(db_session, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies:\n  - canonical_name: NoProvider\n    providers: {}\n")
    with pytest.raises(ValueError, match="no provider slugs"):
        await seed_catalog(db_session, source=bad)
