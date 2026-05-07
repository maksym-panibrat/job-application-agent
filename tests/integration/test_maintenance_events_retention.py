"""Daily maintenance deletes events older than 90 days (spec section 7)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models.event import Event
from app.scheduler.tasks import run_daily_maintenance


@pytest.mark.asyncio
async def test_maintenance_deletes_events_older_than_90_days(db_session):
    now = datetime.now(UTC)
    old = Event(session_id="old-evt", name="x", occurred_at=now - timedelta(days=100))
    fresh = Event(session_id="fresh-evt", name="x", occurred_at=now - timedelta(days=30))
    db_session.add_all([old, fresh])
    await db_session.commit()

    await run_daily_maintenance()

    rows = (
        (
            await db_session.execute(
                select(Event).where(Event.session_id.in_(["old-evt", "fresh-evt"]))
            )
        )
        .scalars()
        .all()
    )
    sids = {r.session_id for r in rows}
    assert "fresh-evt" in sids
    assert "old-evt" not in sids
