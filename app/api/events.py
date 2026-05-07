"""POST /api/events — analytics ingest. Fire-and-forget; returns 204.

Auth required (uses get_current_profile). Anonymous (pre-login) events
are NOT supported in this iteration — anonymous-tracking would need an
optional-auth dep that doesn't exist today; deferred unless needed.

Batches are capped at 50 per request — overflow is dropped silently.
The client (lib/track.ts) batches every 5s, so the cap should never be
hit in practice."""

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.event import Event
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/events", tags=["events"])

MAX_EVENTS_PER_BATCH = 50


class EventIn(BaseModel):
    name: str
    properties: dict | None = None
    path: str | None = None


class EventBatchIn(BaseModel):
    session_id: str
    events: list[EventIn]


@router.post("", status_code=204)
async def log_events(
    body: EventBatchIn,
    request: Request,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    ua = (request.headers.get("user-agent") or "")[:512]

    rows = [
        Event(
            profile_id=profile.id,
            session_id=body.session_id[:64],
            name=ev.name[:64],
            properties=ev.properties,
            user_agent=ua,
            path=(ev.path or "")[:256] or None,
        )
        for ev in body.events[:MAX_EVENTS_PER_BATCH]
    ]
    if rows:
        session.add_all(rows)
        await session.commit()
    # 204: no body
