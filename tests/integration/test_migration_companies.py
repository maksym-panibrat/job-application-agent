"""Integration tests for the company migration tail state."""

import json
import uuid

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_deprecated_target_company_slugs_column_is_removed(db_session):
    rows = (
        await db_session.execute(
            text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'user_profiles'
              AND column_name = 'target_company_slugs'
        """)
        )
    ).all()

    assert rows == []


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
