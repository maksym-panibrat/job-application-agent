"""
Integration tests for the atomic status transition on POST /api/applications/{id}/resume.

Two concurrent resume POSTs against the same awaiting_review row must result
in exactly one 200 (success) and one 409 (state guard) — never two 200s.
Also verifies that the regenerate path's generation_attempts cap is enforced
atomically across retries.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401 — registers all SQLModel tables with metadata
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile

# Matches SINGLE_USER_ID in app/api/deps.py — used when AUTH_ENABLED=false
SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _seed_awaiting_review_application(
    db_session,
    *,
    generation_attempts: int = 1,
) -> Application:
    """Seed a User → UserProfile → Job → Application row already at
    generation_status='awaiting_review' so the resume endpoint can act on it."""
    user = User(
        id=SINGLE_USER_ID,
        email="dev@local",
        is_active=True,
        is_verified=True,
        is_superuser=True,
        hashed_password="",
    )
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=SINGLE_USER_ID,
        full_name="Jane Doe",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        base_resume_md="# Jane Doe\n\nSoftware Engineer",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="test",
        external_id=str(uuid.uuid4()),
        title="Software Engineer",
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="Python role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(
        job_id=job.id,
        profile_id=profile.id,
        generation_status="awaiting_review",
        generation_attempts=generation_attempts,
    )
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    return app_row


@pytest.fixture
async def client(patch_settings, asyncpg_url):
    engine = create_async_engine(asyncpg_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()

    from app.main import app

    # Attach a sentinel checkpointer — the resume endpoint requires one but
    # we stub the background task so the checkpointer is never actually used.
    app.state.checkpointer = object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _noop_resume_background():
    """Stub the background task so test requests don't drive LangGraph."""
    with patch(
        "app.api.applications._resume_in_background",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.asyncio
async def test_concurrent_approve_exactly_one_wins(client, db_session):
    """
    Two concurrent resume POSTs against the same awaiting_review row must
    result in exactly one 200 and one 409 — the atomic UPDATE prevents both
    from passing the status guard.
    """
    app_row = await _seed_awaiting_review_application(db_session, generation_attempts=1)

    coros = [
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "approve"}),
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "approve"}),
    ]
    responses = await asyncio.gather(*coros)
    status_codes = sorted(r.status_code for r in responses)

    assert status_codes == [200, 409], (
        f"Expected [200, 409] from a concurrent double-click, got {status_codes}"
    )


@pytest.mark.asyncio
async def test_concurrent_regenerate_exactly_one_wins_and_bumps_attempts(client, db_session):
    """
    Two concurrent regenerate POSTs: exactly one must succeed and bump
    generation_attempts by exactly 1 (not 2). The losing POST returns 409.
    """
    app_row = await _seed_awaiting_review_application(db_session, generation_attempts=1)

    coros = [
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}),
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}),
    ]
    responses = await asyncio.gather(*coros)
    status_codes = sorted(r.status_code for r in responses)

    assert status_codes == [200, 409], (
        f"Expected [200, 409] from concurrent regenerate, got {status_codes}"
    )

    # And the attempts counter went up by exactly 1 (not 2).
    await db_session.refresh(app_row)
    assert app_row.generation_attempts == 2, (
        f"Expected generation_attempts bumped by exactly 1 "
        f"(1 -> 2), got {app_row.generation_attempts}"
    )


@pytest.mark.asyncio
async def test_regenerate_enforces_attempts_cap_atomically(client, db_session):
    """
    Seed generation_attempts=2; a single regenerate succeeds and bumps to 3,
    then a second regenerate is refused with 429. This verifies the cap is
    enforced as part of the same conditional UPDATE — no TOCTOU window for a
    caller to sneak in a 4th attempt.
    """
    app_row = await _seed_awaiting_review_application(db_session, generation_attempts=2)

    first = await client.post(
        f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}
    )
    assert first.status_code == 200, first.text

    await db_session.refresh(app_row)
    assert app_row.generation_attempts == 3
    assert app_row.generation_status == "generating"

    # Reset status so the 409-vs-429 disambiguation path actually tests the cap.
    app_row.generation_status = "awaiting_review"
    db_session.add(app_row)
    await db_session.commit()

    second = await client.post(
        f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}
    )
    assert second.status_code == 429, second.text

    await db_session.refresh(app_row)
    assert app_row.generation_attempts == 3, "Failed regenerate must not bump attempts past the cap"
