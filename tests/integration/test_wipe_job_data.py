import uuid

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from scripts.wipe_job_data import (
    PRESERVE_TABLES,
    ROW_COUNT_PRESERVE_TABLES,
    WIPE_TABLES,
    wipe,
)

CHECKPOINT_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)


async def _count(db_session, table: str) -> int:
    result = await db_session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
    return result.scalar_one()


async def _seed_reset_rows(db_session) -> None:
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    company_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    feedback_id = uuid.uuid4()
    oauth_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    work_experience_id = uuid.uuid4()
    subscription_plan_id = uuid.uuid4()
    subscription_account_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    subscription_event_id = uuid.uuid4()
    engagement_event_id = uuid.uuid4()
    entitlement_decision_id = uuid.uuid4()
    usage_counter_id = uuid.uuid4()

    await db_session.execute(
        text("""
            INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs,
                resolved_at, created_at)
            VALUES (:company_id, 'Acme', :key, '{"greenhouse":"acme"}', now(), now())
        """),
        {"company_id": company_id, "key": f"acme-{uuid.uuid4()}"},
    )
    await db_session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:user_id, 'wipe@example.com', '', TRUE, FALSE, TRUE)
        """),
        {"user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO user_profiles (id, user_id, email, target_roles, target_locations,
                remote_ok, search_keywords, source_cursors, target_company_slugs,
                target_company_ids, search_active, created_at, updated_at)
            VALUES (:profile_id, :user_id, 'wipe@example.com',
                ARRAY[]::varchar[], ARRAY[]::varchar[], TRUE, ARRAY[]::varchar[],
                '{}'::jsonb, '{}'::jsonb, ARRAY[:company_id]::uuid[], TRUE, now(), now())
        """),
        {"profile_id": profile_id, "user_id": user_id, "company_id": company_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO subscription_plans (id, tier, display_name, followed_company_limit)
            VALUES (:subscription_plan_id, 'paid', 'Paid', 100)
        """),
        {"subscription_plan_id": subscription_plan_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO subscription_accounts (id, user_id, provider, provider_customer_id)
            VALUES (:subscription_account_id, :user_id, 'test', 'cus_wipe')
        """),
        {"subscription_account_id": subscription_account_id, "user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO subscriptions (id, user_id, subscription_account_id, plan_id,
                provider, provider_subscription_id, status, current_period_start,
                current_period_end)
            VALUES (:subscription_id, :user_id, :subscription_account_id,
                :subscription_plan_id, 'test', 'sub_wipe', 'active', now(), now())
        """),
        {
            "subscription_id": subscription_id,
            "user_id": user_id,
            "subscription_account_id": subscription_account_id,
            "subscription_plan_id": subscription_plan_id,
        },
    )
    await db_session.execute(
        text("""
            INSERT INTO subscription_events (id, user_id, subscription_id, event_type,
                provider, provider_event_id, payload)
            VALUES (:subscription_event_id, :user_id, :subscription_id,
                'subscription_created', 'test', 'evt_wipe', '{}'::jsonb)
        """),
        {
            "subscription_event_id": subscription_event_id,
            "user_id": user_id,
            "subscription_id": subscription_id,
        },
    )
    await db_session.execute(
        text("""
            INSERT INTO engagement_events (id, user_id, profile_id, event_type,
                subject_type, subject_id, source, metadata)
            VALUES (:engagement_event_id, :user_id, :profile_id, 'company_followed',
                'company', :company_id, 'test', '{}'::jsonb)
        """),
        {
            "engagement_event_id": engagement_event_id,
            "user_id": user_id,
            "profile_id": profile_id,
            "company_id": company_id,
        },
    )
    await db_session.execute(
        text("""
            INSERT INTO entitlement_decisions (id, user_id, profile_id, decision_type,
                previous_value, next_value, reason)
            VALUES (:entitlement_decision_id, :user_id, :profile_id,
                'paid_entitlement_activated', NULL, '{}'::jsonb, 'wipe test')
        """),
        {
            "entitlement_decision_id": entitlement_decision_id,
            "user_id": user_id,
            "profile_id": profile_id,
        },
    )
    await db_session.execute(
        text("""
            INSERT INTO oauth_accounts (id, user_id, oauth_name, access_token, account_id,
                account_email)
            VALUES (:oauth_id, :user_id, 'google', 'token', 'acct-1', 'wipe@example.com')
        """),
        {"oauth_id": oauth_id, "user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO skills (id, profile_id, name)
            VALUES (:skill_id, :profile_id, 'Python')
        """),
        {"skill_id": skill_id, "profile_id": profile_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO work_experiences (id, profile_id, company, title, start_date,
                technologies)
            VALUES (:work_experience_id, :profile_id, 'Acme', 'Engineer', now(),
                ARRAY[]::varchar[])
        """),
        {"work_experience_id": work_experience_id, "profile_id": profile_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO jobs (id, source, external_id, title, company_name, company_id,
                description_raw, description, apply_url, fetched_at, is_active)
            VALUES (:job_id, 'greenhouse', 'wipe-job', 'Engineer', 'Acme', :company_id,
                '<p>raw</p>', 'raw', 'https://example.com', now(), TRUE)
        """),
        {"job_id": job_id, "company_id": company_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO applications (id, job_id, profile_id, status, generation_status,
                generation_attempts, match_strengths, match_gaps, created_at, updated_at)
            VALUES (:app_id, :job_id, :profile_id, 'pending_review', 'ready',
                0, ARRAY[]::varchar[], ARRAY[]::varchar[], now(), now())
        """),
        {"app_id": app_id, "job_id": job_id, "profile_id": profile_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO generated_documents (id, application_id, doc_type, content_md, created_at)
            VALUES (:doc_id, :app_id, 'cover_letter', 'hello', now())
        """),
        {"doc_id": doc_id, "app_id": app_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO work_queue (job_type, payload, status, dedupe_key)
            VALUES ('match', '{"application_id":"x"}', 'pending', 'match:x')
        """)
    )
    await db_session.execute(
        text("""
            INSERT INTO events (id, profile_id, session_id, name, occurred_at)
            VALUES (:event_id, :profile_id, 'session-1', 'app.opened', now())
        """),
        {"event_id": uuid.uuid4(), "profile_id": profile_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO feedback_reports (id, user_id, user_email, category, message,
                diagnostics, notification_status, created_at)
            VALUES (:feedback_id, :user_id, 'wipe@example.com', 'bug', 'broken',
                '{}'::jsonb, 'not_configured', now())
        """),
        {"feedback_id": feedback_id, "user_id": user_id},
    )
    await db_session.execute(
        text("INSERT INTO llm_status (id, exhausted_until) VALUES (1, now())")
    )
    await db_session.execute(
        text("""
            INSERT INTO rate_limits (key, window_start, count)
            VALUES ('user:wipe', now(), 1)
        """)
    )
    await db_session.execute(
        text("""
            INSERT INTO usage_counters (id, user_id, action, utc_day, count)
            VALUES (:usage_counter_id, :user_id, 'generate', current_date, 1)
        """),
        {"usage_counter_id": usage_counter_id, "user_id": user_id},
    )
    await db_session.commit()


def test_wipe_script_classifies_every_current_model_table():
    import app.models  # noqa: F401

    model_tables = set(SQLModel.metadata.tables)
    classified_tables = set(WIPE_TABLES) | set(PRESERVE_TABLES) | set(
        ROW_COUNT_PRESERVE_TABLES
    )

    assert model_tables == classified_tables


@pytest.mark.asyncio
async def test_wipe_removes_user_owned_and_job_search_rows_but_preserves_companies(
    db_session,
):
    await _seed_reset_rows(db_session)

    await wipe(db_session)

    for table in (
        "generated_documents",
        "applications",
        "jobs",
        "work_queue",
        "events",
        "feedback_reports",
        "entitlement_decisions",
        "engagement_events",
        "subscription_events",
        "subscriptions",
        "subscription_accounts",
        "oauth_accounts",
        "skills",
        "work_experiences",
        "user_profiles",
        "users",
        "llm_status",
        "rate_limits",
        "usage_counters",
    ):
        assert await _count(db_session, table) == 0
    assert await _count(db_session, "companies") == 1
    assert await _count(db_session, "subscription_plans") == 1


@pytest.mark.asyncio
async def test_wipe_resets_non_invalid_slug_fetches_and_preserves_invalid_rows(
    db_session,
):
    await db_session.execute(
        text("""
            INSERT INTO slug_fetches (source, slug, last_fetched_at, last_attempted_at,
                consecutive_404_count, consecutive_5xx_count, is_invalid,
                invalid_reason)
            VALUES
              ('greenhouse', 'validco', now(), now(), 0, 3, FALSE, NULL),
              ('greenhouse', 'deadco', now(), now(), 2, 0, TRUE,
               'board not found')
        """)
    )
    await db_session.commit()

    await wipe(db_session)

    rows = (
        await db_session.execute(
            text("""
                SELECT slug, last_fetched_at, last_attempted_at,
                       consecutive_404_count, consecutive_5xx_count, is_invalid,
                       invalid_reason
                FROM slug_fetches
                ORDER BY slug
            """)
        )
    ).mappings().all()
    by_slug = {row["slug"]: row for row in rows}

    assert by_slug["validco"]["last_fetched_at"] is None
    assert by_slug["validco"]["last_attempted_at"] is None
    assert by_slug["validco"]["consecutive_5xx_count"] == 0

    assert by_slug["deadco"]["is_invalid"] is True
    assert by_slug["deadco"]["consecutive_404_count"] == 2
    assert by_slug["deadco"]["invalid_reason"] == "board not found"


@pytest.mark.asyncio
async def test_wipe_clears_checkpoint_tables_when_present(db_session):
    try:
        for table in CHECKPOINT_TABLES:
            await db_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))  # noqa: S608
            await db_session.execute(text(f"CREATE TABLE {table} (id text primary key)"))  # noqa: S608
            await db_session.execute(text(f"INSERT INTO {table} (id) VALUES ('row-1')"))  # noqa: S608
        await db_session.commit()

        await wipe(db_session)

        for table in CHECKPOINT_TABLES:
            assert await _count(db_session, table) == 0
    finally:
        await db_session.rollback()
        for table in CHECKPOINT_TABLES:
            await db_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))  # noqa: S608
        await db_session.commit()


@pytest.mark.asyncio
async def test_wipe_tolerates_absent_checkpoint_tables(db_session):
    await _seed_reset_rows(db_session)

    await wipe(db_session)

    assert await _count(db_session, "users") == 0


@pytest.mark.asyncio
async def test_wipe_rolls_back_when_failure_is_injected(db_session):
    await _seed_reset_rows(db_session)
    await db_session.execute(
        text("""
            INSERT INTO slug_fetches (source, slug, last_fetched_at,
                consecutive_404_count, consecutive_5xx_count, is_invalid)
            VALUES ('greenhouse', 'validco', now(), 0, 2, FALSE)
        """)
    )
    await db_session.commit()

    with pytest.raises(RuntimeError, match="injected failure after reset mutation"):
        await wipe(db_session, fail_after_mutation=True)

    assert await _count(db_session, "users") == 1
    assert await _count(db_session, "jobs") == 1
    row = (
        await db_session.execute(
            text(
                "SELECT last_fetched_at, consecutive_5xx_count "
                "FROM slug_fetches WHERE slug = 'validco'"
            )
        )
    ).one()
    assert row.last_fetched_at is not None
    assert row.consecutive_5xx_count == 2
