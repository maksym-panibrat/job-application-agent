"""Unit test for the manual job-sync daily quota constant.

The previous limit of 1/day made the in-app "Sync jobs" button effectively
unusable after the first click of the day (429 every subsequent click).
This test enforces a usable lower bound and prevents future regressions.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
os.environ.setdefault("ENVIRONMENT", "test")


def test_manual_sync_daily_limit_is_usable():
    """Ceiling must allow more than one click per day; 1/day was the prior bug."""
    from app.api.jobs import MANUAL_SYNC_DAILY_LIMIT

    assert MANUAL_SYNC_DAILY_LIMIT >= 10, (
        f"Daily quota for manual sync is too low: {MANUAL_SYNC_DAILY_LIMIT}. "
        "1/day made the UI button effectively unusable. Use ≥ 10."
    )
