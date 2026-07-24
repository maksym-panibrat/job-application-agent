"""Enqueue one mechanically bounded batch-match canary for an isolated profile."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_
from sqlmodel import col, select

from app.contracts.workflow import JobType, batch_match_dedupe_key
from app.database import get_session_factory
from app.models.llm_match_batch import ACTIVE_BATCH_STATUSES, LLMMatchBatch
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.enqueue_intents import enqueue_batch_match

CANARY_MAX_ITEMS = 10


class CanaryRefused(RuntimeError):
    """The requested profile is not idle enough for an isolated canary."""


async def _assert_no_active_provider_batch(session, *, profile_id: uuid.UUID) -> None:
    batch_result = await session.execute(
        select(LLMMatchBatch.id)
        .where(
            LLMMatchBatch.profile_id == profile_id,
            col(LLMMatchBatch.status).in_(ACTIVE_BATCH_STATUSES),
        )
        .limit(1)
    )
    if batch_result.scalar_one_or_none() is not None:
        raise CanaryRefused("profile already has an active provider batch")


async def _assert_profile_idle(session, *, profile_id: uuid.UUID) -> None:
    queue_result = await session.execute(
        select(WorkQueue.id)
        .where(
            WorkQueue.job_type == JobType.BATCH_MATCH,
            col(WorkQueue.status).in_(
                (WorkQueueStatus.PENDING, WorkQueueStatus.IN_PROGRESS)
            ),
            or_(
                col(WorkQueue.dedupe_key) == batch_match_dedupe_key(profile_id),
                col(WorkQueue.payload)["profile_id"].as_string() == str(profile_id),
            ),
        )
        .limit(1)
    )
    if queue_result.scalar_one_or_none() is not None:
        raise CanaryRefused("profile already has pending/in-progress batch-match queue work")

    await _assert_no_active_provider_batch(session, profile_id=profile_id)


async def enqueue_batch_match_canary(
    session,
    *,
    profile_id: uuid.UUID,
    max_items: int,
) -> int:
    await _assert_profile_idle(session, profile_id=profile_id)
    row_id = await enqueue_batch_match(
        session,
        profile_id,
        max_items=max_items,
        max_candidates=max_items,
        return_existing_on_conflict=False,
    )
    if row_id is None:
        raise CanaryRefused("concurrent batch-match queue work appeared; nothing was changed")
    try:
        # Close the race where an older queue row finishes and creates a provider
        # batch between the initial idle check and our insert.
        await _assert_no_active_provider_batch(session, profile_id=profile_id)
    except CanaryRefused:
        await session.rollback()
        raise
    await session.commit()
    return row_id


async def lookup_profile_id_by_email(session, *, email: str) -> uuid.UUID:
    result = await session.execute(
        select(UserProfile.id)
        .join(User, UserProfile.user_id == User.id)
        .where(User.email == email)
        .limit(1)
    )
    profile_id = result.scalar_one_or_none()
    if profile_id is None:
        raise SystemExit(f"No profile found for user email {email!r}")
    return profile_id


async def main(*, profile_id_arg: str | None, email: str | None, max_items: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        profile_id = (
            uuid.UUID(profile_id_arg)
            if profile_id_arg is not None
            else await lookup_profile_id_by_email(session, email=email or "")
        )
        if await session.get(UserProfile, profile_id) is None:
            raise SystemExit(f"No profile found for profile ID {profile_id}")
        try:
            row_id = await enqueue_batch_match_canary(
                session,
                profile_id=profile_id,
                max_items=max_items,
            )
        except CanaryRefused as exc:
            raise SystemExit(f"Canary refused: {exc}") from exc
    print(
        f"enqueued bounded batch-match row_id={row_id} profile_id={profile_id} "
        f"max_items={max_items} max_candidates={max_items}"
    )


def _canary_item_count(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= value <= CANARY_MAX_ITEMS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {CANARY_MAX_ITEMS}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--profile-id", dest="profile_id_arg", help="User profile UUID")
    target.add_argument("--email", help="Account email to resolve to user_profiles.id")
    parser.add_argument(
        "--max-items",
        type=_canary_item_count,
        required=True,
        help=f"maximum applications inspected and submitted (required; 1-{CANARY_MAX_ITEMS})",
    )
    parser.add_argument(
        "--ack-isolated-canary-profile",
        action="store_true",
        required=True,
        help="confirm this is a disposable synthetic profile with no concurrent real-user activity",
    )
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    asyncio.run(
        main(
            profile_id_arg=args.profile_id_arg,
            email=args.email,
            max_items=args.max_items,
        )
    )
