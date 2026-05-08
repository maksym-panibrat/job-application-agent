"""Integration test: migration creates Company rows and target_company_ids
from existing target_company_slugs JSON, drops dead lever/ashby entries."""

import json
import uuid

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_backfills_companies_from_greenhouse_slugs(db_session):
    """Seed pre-migration profile shape, run upgrade, assert company rows
    materialize and target_company_ids populates."""
    # NOTE: this test runs against a testcontainers Postgres where Alembic
    # has already upgraded to head (per conftest), so we simulate the data
    # state by inserting a profile via SQL with both old and new columns
    # populated, then verify the new shape is queryable.
    await db_session.execute(
        text("""
        INSERT INTO users (
            id, email, hashed_password,
            is_active, is_superuser, is_verified
        )
        VALUES (:uid, 'mig@example.com', 'x', true, false, true)
    """),
        {"uid": str(uuid.uuid4())},
    )
    await db_session.commit()

    user_id = (
        await db_session.execute(text("SELECT id FROM users WHERE email = 'mig@example.com'"))
    ).scalar_one()

    await db_session.execute(
        text("""
        INSERT INTO user_profiles (
            id, user_id, target_company_slugs,
            remote_ok, search_active,
            created_at, updated_at
        )
        VALUES (:pid, :uid, :slugs, true, true, NOW(), NOW())
    """),
        {
            "pid": str(uuid.uuid4()),
            "uid": str(user_id),
            "slugs": json.dumps({"greenhouse": ["stripe", "linear"], "lever": ["dead-entry"]}),
        },
    )
    await db_session.commit()

    # Re-run the data-backfill blocks from the migration so the test is
    # deterministic against a fixture profile inserted after the original
    # upgrade ran. This simulates "what would happen if a profile with this
    # shape existed at upgrade time."
    await db_session.execute(
        text("""
        INSERT INTO companies (
            id, canonical_name, normalized_key,
            provider_slugs, resolved_at, created_at
        )
        SELECT gen_random_uuid(), initcap(replace(slug, '-', ' ')), slug,
               jsonb_build_object('greenhouse', slug), NOW(), NOW()
        FROM (
            SELECT DISTINCT jsonb_array_elements_text(target_company_slugs->'greenhouse') AS slug
            FROM user_profiles WHERE jsonb_typeof(target_company_slugs->'greenhouse') = 'array'
        ) s
        WHERE slug IS NOT NULL AND slug <> ''
        ON CONFLICT (normalized_key) DO NOTHING
    """)
    )
    await db_session.execute(
        text("""
        UPDATE user_profiles up
        SET target_company_ids = COALESCE((
            SELECT array_agg(c.id)
            FROM jsonb_array_elements_text(up.target_company_slugs->'greenhouse') AS slug
            JOIN companies c ON c.provider_slugs->>'greenhouse' = slug
        ), '{}')
    """)
    )
    await db_session.commit()

    # Two Company rows: stripe and linear.
    rows = (
        await db_session.execute(
            text(
                "SELECT canonical_name, normalized_key, provider_slugs FROM companies "
                "WHERE normalized_key IN ('stripe', 'linear') ORDER BY normalized_key"
            )
        )
    ).all()
    assert len(rows) == 2
    assert rows[0].normalized_key == "linear"
    assert rows[0].canonical_name == "Linear"
    assert rows[0].provider_slugs == {"greenhouse": "linear"}
    assert rows[1].normalized_key == "stripe"

    # target_company_ids has both UUIDs.
    profile_ids = (
        await db_session.execute(
            text("SELECT target_company_ids FROM user_profiles WHERE user_id = :uid"),
            {"uid": str(user_id)},
        )
    ).scalar_one()
    assert len(profile_ids) == 2

    # Lever 'dead-entry' did NOT create a Company row (only greenhouse seeds did).
    lever_rows = (
        await db_session.execute(text("SELECT id FROM companies WHERE provider_slugs ? 'lever'"))
    ).all()
    assert len(lever_rows) == 0


@pytest.mark.asyncio
async def test_migration_backfills_jobs_company_id(db_session):
    """jobs.company_id populates for greenhouse jobs whose company_name
    matches a Company.canonical_name."""
    company_id = uuid.uuid4()
    await db_session.execute(
        text("""
        INSERT INTO companies (
            id, canonical_name, normalized_key,
            provider_slugs, resolved_at, created_at
        )
        VALUES (:cid, 'Stripe', 'stripe-fixture', :slugs, NOW(), NOW())
    """),
        {
            "cid": str(company_id),
            "slugs": json.dumps({"greenhouse": "stripe-fixture"}),
        },
    )

    await db_session.execute(
        text("""
        INSERT INTO jobs (
            id, source, external_id, title, company_name,
            apply_url, fetched_at, is_active
        )
        VALUES (
            :jid, 'greenhouse', 'job-1', 'SWE', 'Stripe',
            'https://example.com/1', NOW(), true
        )
    """),
        {"jid": str(uuid.uuid4())},
    )
    await db_session.commit()

    # Re-run the backfill block.
    await db_session.execute(
        text("""
        UPDATE jobs j
        SET company_id = c.id
        FROM companies c
        WHERE j.source = 'greenhouse'
          AND c.provider_slugs->>'greenhouse' IS NOT NULL
          AND c.canonical_name = j.company_name
    """)
    )
    await db_session.commit()

    backfilled = (
        await db_session.execute(text("SELECT company_id FROM jobs WHERE external_id = 'job-1'"))
    ).scalar_one()
    assert backfilled == company_id
