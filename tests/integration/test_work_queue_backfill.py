"""Validates Migration 1's data backfill SQL by importing and executing the
three backfill-SQL constants exported from the migration module against
seeded legacy-queue state. db_session's per-test schema teardown handles cleanup.

The migration's upgrade() body calls op.execute() on these same constants —
single source of truth, no copy-paste drift between test and migration."""
import importlib.util
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text


def _load_migration_constants():
    """Import the Phase B Migration 1 module by glob, return its three SQL constants.
    Glob avoids hardcoding the auto-generated revision ID."""
    root = pathlib.Path(__file__).resolve().parents[2]
    matches = list((root / "alembic" / "versions").glob("*phase_b_migration_1*.py"))
    assert len(matches) == 1, f"expected exactly 1 Migration 1 file, got {matches}"
    spec = importlib.util.spec_from_file_location("phase_b_mig1", matches[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return (
        mod.BACKFILL_FETCH_SLUG_SQL,
        mod.BACKFILL_MATCH_SQL,
        mod.BACKFILL_GENERATION_SQL,
    )


async def _run_backfill(db_session):
    fetch_sql, match_sql, gen_sql = _load_migration_constants()
    await db_session.execute(text(fetch_sql))
    await db_session.execute(text(match_sql))
    await db_session.execute(text(gen_sql))
    await db_session.commit()


async def _seed_user_profile_and_job(db_session) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a User → UserProfile → Job triple and return (profile_id, job_id).
    Uses raw SQL so we don't have to import the ORM classes here."""
    user_id, profile_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_session.execute(
        text(
            """
        INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
        VALUES (:uid, :email, '', true, false, false)
    """
        ),
        {"uid": user_id, "email": f"u-{user_id}@local"},
    )
    await db_session.execute(
        text(
            """
        INSERT INTO user_profiles (id, user_id, remote_ok, search_active, created_at, updated_at)
        VALUES (:pid, :uid, true, true, now(), now())
    """
        ),
        {"pid": profile_id, "uid": user_id},
    )
    await db_session.execute(
        text(
            """
        INSERT INTO jobs (id, source, external_id, title, company_name, apply_url, fetched_at, is_active)
        VALUES (:jid, 'greenhouse', :ext, 'Engineer', 'Co', 'https://x/', now(), true)
    """
        ),
        {"jid": job_id, "ext": f"ext-{job_id}"},
    )
    await db_session.commit()
    return profile_id, job_id


@pytest.mark.asyncio
async def test_backfill_fetch_slug_includes_dead_claims_and_skips_recent_and_invalid(
    db_session,
):
    """Slug rows with queued_at and (claimed_at IS NULL OR > 300s ago) get fetch-slug
    rows; recent-claim and invalid rows are skipped."""
    await db_session.execute(
        text(
            """
        INSERT INTO slug_fetches (source, slug, queued_at, claimed_at, is_invalid,
                                  consecutive_404_count, consecutive_5xx_count)
        VALUES
          ('greenhouse', 'old-dead', now() - interval '10 hours',
              now() - interval '400 seconds', false, 0, 0),
          ('greenhouse', 'fresh-pending', now() - interval '10 minutes',
              NULL, false, 0, 0),
          ('greenhouse', 'recent-claim-skipped', now() - interval '10 minutes',
              now() - interval '100 seconds', false, 0, 0),
          ('greenhouse', 'invalid-skipped', now() - interval '1 day',
              NULL, true, 0, 0);
    """
        )
    )
    await db_session.commit()

    await _run_backfill(db_session)

    rows = (
        await db_session.execute(
            text(
                """
        SELECT payload->>'slug' AS slug FROM work_queue
        WHERE job_type = 'fetch-slug' ORDER BY slug
    """
            )
        )
    ).all()
    assert [r[0] for r in rows] == ["fresh-pending", "old-dead"]


@pytest.mark.asyncio
async def test_backfill_match_carries_attempts(db_session):
    """Matches in pending_match with old-enough match_claimed_at re-enqueued with
    attempts preserved."""
    profile_id, job_id = await _seed_user_profile_and_job(db_session)
    app_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
        INSERT INTO applications (id, job_id, profile_id, status,
                                  generation_status, generation_attempts,
                                  match_status, match_queued_at, match_claimed_at, match_attempts,
                                  match_strengths, match_gaps, created_at, updated_at)
        VALUES (:aid, :jid, :pid, 'pending_review',
                'none', 0,
                'pending_match', now() - interval '1 hour',
                now() - interval '500 seconds', 2,
                '{}', '{}', now(), now())
    """
        ),
        {"aid": app_id, "jid": job_id, "pid": profile_id},
    )
    await db_session.commit()

    await _run_backfill(db_session)

    row = (
        await db_session.execute(
            text(
                """
        SELECT attempts, dedupe_key FROM work_queue WHERE job_type='match'
    """
            )
        )
    ).first()
    assert row is not None
    assert row[0] == 2
    assert row[1] == f"match:{app_id}"


@pytest.mark.asyncio
async def test_backfill_generation_includes_all_pending(db_session):
    """Two pending generations enqueued; the one in 'ready' state is not."""
    profile_id, _ = await _seed_user_profile_and_job(db_session)
    # 3 distinct jobs for 3 applications.
    job_ids = [uuid.uuid4() for _ in range(3)]
    for jid in job_ids:
        await db_session.execute(
            text(
                """
            INSERT INTO jobs (id, source, external_id, title, company_name, apply_url, fetched_at, is_active)
            VALUES (:jid, 'greenhouse', :ext, 'E', 'Co', 'https://x/', now(), true)
        """
            ),
            {"jid": jid, "ext": f"ext-{jid}"},
        )
    for jid, gs in zip(job_ids, ("pending", "pending", "ready"), strict=False):
        await db_session.execute(
            text(
                """
            INSERT INTO applications (id, job_id, profile_id, status, generation_status,
                                      generation_attempts, match_status, match_attempts,
                                      match_strengths, match_gaps,
                                      created_at, updated_at)
            VALUES (:aid, :jid, :pid, 'pending_review', :gs, 1,
                    'matched', 0,
                    '{}', '{}', now(), now())
        """
            ),
            {"aid": uuid.uuid4(), "jid": jid, "pid": profile_id, "gs": gs},
        )
    await db_session.commit()

    await _run_backfill(db_session)

    count = (
        await db_session.execute(
            text(
                """
        SELECT count(*) FROM work_queue WHERE job_type='generate-cover-letter'
    """
            )
        )
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_session):
    """Re-running the three INSERTs on a populated work_queue is a no-op
    (ON CONFLICT DO NOTHING on the partial unique index)."""
    await db_session.execute(
        text(
            """
        INSERT INTO slug_fetches (source, slug, queued_at, is_invalid,
                                  consecutive_404_count, consecutive_5xx_count)
        VALUES ('greenhouse', 'a', now(), false, 0, 0);
    """
        )
    )
    await db_session.commit()

    await _run_backfill(db_session)
    await _run_backfill(db_session)  # second pass must be a no-op

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM work_queue WHERE job_type='fetch-slug'")
        )
    ).scalar_one()
    assert count == 1


def test_migration_upgrade_actually_calls_all_three_constants():
    """Importing and executing the SQL constants only proves the constants are correct
    — not that the migration's upgrade() body actually CALLS them. Spy on op.execute
    and run upgrade() against a mocked op; assert all three SQL constants were
    passed to op.execute at least once."""
    root = pathlib.Path(__file__).resolve().parents[2]
    matches = list((root / "alembic" / "versions").glob("*phase_b_migration_1*.py"))
    spec = importlib.util.spec_from_file_location("phase_b_mig1", matches[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    executed_sql: list[str] = []
    mock_op = MagicMock()

    def _record_execute(sql):
        executed_sql.append(str(sql).strip())

    mock_op.execute.side_effect = _record_execute
    # Patch the table/index creation calls to no-op so upgrade() runs even though
    # the work_queue table already exists in db_session's schema. We only assert
    # the three backfill SQL strings get passed to op.execute.
    with patch.object(mod, "op", mock_op):
        mod.upgrade()

    assert any(mod.BACKFILL_FETCH_SLUG_SQL.strip() in s for s in executed_sql), (
        "upgrade() did not call op.execute(BACKFILL_FETCH_SLUG_SQL)"
    )
    assert any(mod.BACKFILL_MATCH_SQL.strip() in s for s in executed_sql), (
        "upgrade() did not call op.execute(BACKFILL_MATCH_SQL)"
    )
    assert any(mod.BACKFILL_GENERATION_SQL.strip() in s for s in executed_sql), (
        "upgrade() did not call op.execute(BACKFILL_GENERATION_SQL)"
    )
