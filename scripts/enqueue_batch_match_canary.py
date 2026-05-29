"""Enqueue one manual batch-match canary job for a user profile."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.database import get_session_factory
from app.models.user import User
from app.models.user_profile import UserProfile
from app.worker.queue_service import enqueue


async def enqueue_batch_match_canary(session, *, profile_id: uuid.UUID) -> int | None:
    row_id = await enqueue(
        session,
        job_type="batch-match",
        payload={"profile_id": str(profile_id)},
        dedupe_key=f"batch-match:{profile_id}",
        on_conflict="upsert_reset_not_before",
    )
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


async def main(*, profile_id_arg: str | None, email: str | None) -> None:
    factory = get_session_factory()
    async with factory() as session:
        profile_id = (
            uuid.UUID(profile_id_arg)
            if profile_id_arg is not None
            else await lookup_profile_id_by_email(session, email=email or "")
        )
        row_id = await enqueue_batch_match_canary(session, profile_id=profile_id)
    print(f"enqueued batch-match row_id={row_id} profile_id={profile_id}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--profile-id", dest="profile_id_arg", help="User profile UUID")
    target.add_argument("--email", help="Account email to resolve to user_profiles.id")
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    asyncio.run(main(profile_id_arg=args.profile_id_arg, email=args.email))
