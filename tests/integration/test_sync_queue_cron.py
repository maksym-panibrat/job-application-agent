"""Integration test for run_sync_queue cron worker."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_sync_queue
from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


async def _seed_profile(db_session, *slugs: str) -> UserProfile:
    """Seed a User + UserProfile (FK constraint requires the user row first)."""
    user = User(id=uuid.uuid4(), email=f"sync-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    profile = UserProfile(
        user_id=user.id,
        target_company_slugs={"greenhouse": list(slugs)},
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_run_sync_queue_fetches_claimed_slugs_and_enqueues_matches(db_session):
    profile = await _seed_profile(db_session, "airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    fixture = {
        "jobs": [
            {
                "id": 9001,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/9001",
                "updated_at": datetime.now(UTC).isoformat(),
                "content": "<p>job</p>",
            }
        ]
    }
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb/jobs").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        result = await run_sync_queue()

    assert result["fetched"] == 1
    jobs = (await db_session.execute(sa.select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].company_name == slug_to_company_name("airbnb")
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 1
    assert apps[0].match_status == "pending_match"


@pytest.mark.asyncio
async def test_run_sync_queue_marks_invalid_after_2_404s(db_session):
    profile = await _seed_profile(db_session, "openai")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(return_value=httpx.Response(404))
        await run_sync_queue()
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False

    # Re-queue + run again
    row.queued_at = datetime.now(UTC)
    await db_session.commit()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(return_value=httpx.Response(404))
        await run_sync_queue()
    # Drop in-memory cache so we re-read the row state the worker (in a separate
    # session) committed.
    db_session.expire_all()
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True
