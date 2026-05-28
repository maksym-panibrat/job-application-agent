"""Helpers for recording server-authored active engagement events."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import EngagementEvent


async def record_engagement(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
    event_type: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    source: str = "api",
    metadata: dict[str, Any] | None = None,
) -> EngagementEvent:
    event = EngagementEvent(
        user_id=user_id,
        profile_id=profile_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source,
        event_metadata=metadata or {},
    )
    session.add(event)
    await session.flush()
    return event
