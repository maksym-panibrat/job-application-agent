"""Idempotently seed a demo profile under SINGLE_USER_ID."""

import asyncio
import json
import uuid
from pathlib import Path

from app.config import get_settings
from app.database import get_session_factory
from app.models import User
from app.services.profile_service import (
    get_or_create_profile,
    replace_all_skills,
    replace_all_work_experiences,
    update_profile,
)

SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEMO_PROFILE_PATH = Path(__file__).parent.parent / "demo_profile.json"


async def main() -> None:
    get_settings()  # validate config early

    data = json.loads(DEMO_PROFILE_PATH.read_text())
    skills = data.pop("skills", [])
    work_experiences = data.pop("work_experiences", [])

    factory = get_session_factory()
    async with factory() as session:
        # Ensure the dev user row exists
        from sqlmodel import select
        result = await session.execute(select(User).where(User.id == SINGLE_USER_ID))
        if result.scalar_one_or_none() is None:
            session.add(User(
                id=SINGLE_USER_ID,
                email="dev@local",
                is_active=True,
                is_verified=True,
                is_superuser=True,
                hashed_password="",
            ))
            await session.commit()

        profile = await get_or_create_profile(SINGLE_USER_ID, session)
        await update_profile(profile.id, data, session)

        if skills:
            await replace_all_skills(profile.id, skills, session)

        if work_experiences:
            await replace_all_work_experiences(profile.id, work_experiences, session)

    print(f"Demo profile seeded for user {SINGLE_USER_ID}")


if __name__ == "__main__":
    asyncio.run(main())
