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


async def _seed_awaiting_review_application(
    db_session,
    profile,  # passed from caller (seeded_user fixture)
    *,
    generation_attempts: int = 1,
) -> Application:
    """Seed a Job + Application row at generation_status='awaiting_review'."""
    # Top up profile fields the existing tests relied on
    if not profile.full_name:
        profile.full_name = "Jane Doe"
        profile.first_name = "Jane"
        profile.last_name = "Doe"
        profile.base_resume_md = "# Jane Doe\n\nSoftware Engineer"
        profile.target_roles = ["Software Engineer"]
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
    # ``app`` is a module-level singleton so we must restore the prior value
    # on teardown; otherwise other test files (notably tests/e2e/*) that
    # reuse the same app end up with this bogus checkpointer and fail when
    # LangGraph type-checks it.
    _sentinel = object()
    _prev_checkpointer = getattr(app.state, "checkpointer", _sentinel)
    app.state.checkpointer = object()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        if _prev_checkpointer is _sentinel:
            if hasattr(app.state, "checkpointer"):
                delattr(app.state, "checkpointer")
        else:
            app.state.checkpointer = _prev_checkpointer


@pytest.fixture(autouse=True)
def _noop_resume_background():
    """Stub the background task so test requests don't drive LangGraph."""
    with patch(
        "app.api.applications._resume_in_background",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.asyncio
async def test_concurrent_approve_exactly_one_wins(client, db_session, seeded_user, auth_headers):
    """
    Two concurrent resume POSTs against the same awaiting_review row must
    result in exactly one 200 and one 409 — the atomic UPDATE prevents both
    from passing the status guard.
    """
    _, profile = seeded_user
    app_row = await _seed_awaiting_review_application(db_session, profile, generation_attempts=1)

    coros = [
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "approve"}, headers=auth_headers),
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "approve"}, headers=auth_headers),
    ]
    responses = await asyncio.gather(*coros)
    status_codes = sorted(r.status_code for r in responses)

    assert status_codes == [200, 409], (
        f"Expected [200, 409] from a concurrent double-click, got {status_codes}"
    )


@pytest.mark.asyncio
async def test_concurrent_regenerate_exactly_one_wins_and_bumps_attempts(client, db_session, seeded_user, auth_headers):
    """
    Two concurrent regenerate POSTs: exactly one must succeed and bump
    generation_attempts by exactly 1 (not 2). The losing POST returns 409.
    """
    _, profile = seeded_user
    app_row = await _seed_awaiting_review_application(db_session, profile, generation_attempts=1)

    coros = [
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}, headers=auth_headers),
        client.post(f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}, headers=auth_headers),
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
async def test_regenerate_enforces_attempts_cap_atomically(client, db_session, seeded_user, auth_headers):
    """
    Seed generation_attempts=2; a single regenerate succeeds and bumps to 3,
    then a second regenerate is refused with 429. This verifies the cap is
    enforced as part of the same conditional UPDATE — no TOCTOU window for a
    caller to sneak in a 4th attempt.
    """
    _, profile = seeded_user
    app_row = await _seed_awaiting_review_application(db_session, profile, generation_attempts=2)

    first = await client.post(
        f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}, headers=auth_headers
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
        f"/api/applications/{app_row.id}/resume", json={"decision": "regenerate"}, headers=auth_headers
    )
    assert second.status_code == 429, second.text

    await db_session.refresh(app_row)
    assert app_row.generation_attempts == 3, "Failed regenerate must not bump attempts past the cap"
