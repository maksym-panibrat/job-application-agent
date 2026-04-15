import os
from datetime import UTC, datetime, timedelta


def setup_env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def is_expired(search_expires_at: datetime | None) -> bool:
    """Check if a search_expires_at timestamp is in the past."""
    if search_expires_at is None:
        return False
    now = datetime.now(tz=UTC)
    # Handle naive datetimes (stored without tz in DB)
    if search_expires_at.tzinfo is None:
        search_expires_at = search_expires_at.replace(tzinfo=UTC)
    return now > search_expires_at


def test_expired_in_past():
    past = datetime.now(tz=UTC) - timedelta(hours=1)
    assert is_expired(past) is True


def test_not_expired_in_future():
    future = datetime.now(tz=UTC) + timedelta(days=7)
    assert is_expired(future) is False


def test_none_not_expired():
    assert is_expired(None) is False
