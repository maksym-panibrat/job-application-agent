import uuid

import pytest
from sqlmodel import select

from app.contracts.workflow import JobType, batch_match_dedupe_key
from app.models.llm_match_batch import BATCH_STATUS_SUBMITTED, LLMMatchBatch
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from scripts.enqueue_batch_match_canary import CanaryRefused, enqueue_batch_match_canary


async def _profile(db_session) -> UserProfile:
    user = User(email=f"canary-{uuid.uuid4()}@example.test")
    db_session.add(user)
    await db_session.flush()
    profile = UserProfile(user_id=user.id, full_name="Synthetic Canary")
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest.mark.asyncio
async def test_canary_writes_exact_bounded_payload(db_session):
    profile = await _profile(db_session)

    row_id = await enqueue_batch_match_canary(
        db_session,
        profile_id=profile.id,
        max_items=4,
    )

    row = (await db_session.execute(select(WorkQueue).where(WorkQueue.id == row_id))).scalar_one()
    assert row.job_type == JobType.BATCH_MATCH
    assert row.dedupe_key == batch_match_dedupe_key(profile.id)
    assert row.payload == {
        "profile_id": str(profile.id),
        "max_items": 4,
        "max_candidates": 4,
    }


@pytest.mark.asyncio
async def test_canary_refuses_live_queue_row_without_reset(db_session):
    profile = await _profile(db_session)
    existing = WorkQueue(
        job_type=JobType.BATCH_MATCH,
        payload={"profile_id": str(profile.id), "max_items": 2},
        status=WorkQueueStatus.PENDING,
        dedupe_key=batch_match_dedupe_key(profile.id),
    )
    db_session.add(existing)
    await db_session.commit()

    with pytest.raises(CanaryRefused, match="pending/in-progress"):
        await enqueue_batch_match_canary(
            db_session,
            profile_id=profile.id,
            max_items=4,
        )

    await db_session.refresh(existing)
    assert existing.payload == {"profile_id": str(profile.id), "max_items": 2}
    assert existing.status == WorkQueueStatus.PENDING


@pytest.mark.asyncio
async def test_canary_refuses_active_provider_batch(db_session):
    profile = await _profile(db_session)
    batch = LLMMatchBatch(
        profile_id=profile.id,
        provider="fake",
        model="fake-model",
        prompt_version="canary-test",
        status=BATCH_STATUS_SUBMITTED,
    )
    db_session.add(batch)
    await db_session.commit()

    with pytest.raises(CanaryRefused, match="active provider batch"):
        await enqueue_batch_match_canary(
            db_session,
            profile_id=profile.id,
            max_items=4,
        )

    rows = (await db_session.execute(select(WorkQueue))).scalars().all()
    assert rows == []
