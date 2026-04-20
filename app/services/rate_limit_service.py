"""
Simple Postgres-backed rate limiter and usage quota checker.
Uses INSERT ... ON CONFLICT DO UPDATE to atomically increment counters.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rate_limits import RateLimit
from app.models.usage_counters import UsageCounter


def _window_start(window_seconds: int) -> datetime:
    now = datetime.now(tz=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = (now - epoch).total_seconds()
    boundary = elapsed - (elapsed % window_seconds)
    return epoch + timedelta(seconds=boundary)


async def check_rate_limit(
    key: str,
    limit: int,
    window_seconds: int,
    session: AsyncSession,
) -> None:
    """Raise HTTP 429 if key has exceeded limit requests in the window."""
    window = _window_start(window_seconds)
    stmt = pg_insert(RateLimit).values(key=key, window_start=window, count=1)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_rate_limits_key_window",
        set_={"count": RateLimit.count + 1},
    ).returning(RateLimit.count)
    result = await session.execute(stmt)
    await session.commit()
    count = result.scalar_one()
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(window_seconds)},
        )


async def check_daily_quota(
    user_id: uuid.UUID,
    action: str,
    limit: int,
    session: AsyncSession,
) -> None:
    """Raise HTTP 429 if user has exceeded daily limit for this action."""
    today = date.today()
    stmt = pg_insert(UsageCounter).values(
        user_id=user_id, action=action, utc_day=today, count=1
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_usage_counters_user_action_day",
        set_={"count": UsageCounter.count + 1},
    ).returning(UsageCounter.count)
    result = await session.execute(stmt)
    await session.commit()
    count = result.scalar_one()
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit of {limit} for '{action}' reached. Try again tomorrow.",
            headers={"Retry-After": "86400"},
        )
