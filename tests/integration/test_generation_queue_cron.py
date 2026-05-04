"""Integration tests for run_generation_queue cron worker."""

import asyncio
import time
import uuid
from unittest.mock import patch

import pytest

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_generation_queue


async def _seed_pending_app(db_session) -> Application:
    """User → Profile → Job → Application(generation_status='pending')."""
    user = User(id=uuid.uuid4(), email=f"genq-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(user_id=user.id, email=user.email)
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="greenhouse_board",
        external_id=str(uuid.uuid4()),
        title="Engineer",
        company_name="Acme",
        apply_url="https://x",
        description_md="role",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(
        job_id=job.id,
        profile_id=profile.id,
        generation_status="pending",
    )
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)
    return app_row


@pytest.mark.asyncio
async def test_run_generation_queue_calls_generate_materials_with_correct_signature(db_session):
    """Latent bug discovered while working #76: run_generation_queue passes
    `checkpointer=...` to generate_materials, but the function's signature
    is just (application_id, session). The TypeError is silently caught by
    the generic `except Exception` and counted as `failed`, so the cron
    looks healthy in CI but every pending app fails to generate in prod.

    This test exercises the actual call path (not a mock of run_generation_queue
    itself) to catch the TypeError."""
    await _seed_pending_app(db_session)
    received_kwargs: dict = {}

    async def fake_generate(application_id, session):
        # Capture what kwargs we got — none should be passed beyond positional args.
        received_kwargs["application_id"] = application_id
        received_kwargs["called"] = True
        return None

    with patch(
        "app.services.application_service.generate_materials",
        side_effect=fake_generate,
    ):
        result = await run_generation_queue()

    assert received_kwargs.get("called"), (
        "generate_materials was never invoked successfully — likely TypeError "
        "from an unexpected keyword argument. result={result}"
    )
    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_run_generation_queue_respects_deadline_and_surfaces_deferred(db_session):
    """Per-tick deadline must short-circuit further iterations. Without it,
    10 pending apps × ~10-30s per generate_materials call ≈ 100-300s, right at
    Cloud Run's 300s wall (#76)."""
    # Seed 3 pending apps
    for _ in range(3):
        await _seed_pending_app(db_session)

    call_times: list[float] = []

    async def slow_generate(application_id, session):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.4)  # each call exceeds the 0.3s deadline alone
        return None

    with patch(
        "app.services.application_service.generate_materials",
        side_effect=slow_generate,
    ):
        result = await run_generation_queue(deadline_seconds=0.3)

    # First call always fires (deadline check is at top of loop, deadline still
    # ahead). After it, 0.4s elapsed > 0.3s deadline → break, defer the rest.
    assert len(call_times) == 1, (
        f"expected exactly 1 call before the deadline trips, got {len(call_times)}"
    )
    assert result["attempted"] == 3
    assert result["succeeded"] == 1
    assert result["deferred"] == 2, (
        f"deferred count must surface so callers know there's queued work left; got {result}"
    )
