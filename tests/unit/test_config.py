def test_settings_defaults():
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
    from app.config import Settings

    s = Settings()
    assert s.match_score_threshold == 0.65
    assert s.max_matches_displayed == 20


def test_job_stale_after_days_default_is_21():
    """Stale TTL bumped from 14d to 21d (spec 2026-04-28)."""
    from app.config import Settings

    s = Settings(database_url="postgresql://x:x@x/x")
    assert s.job_stale_after_days == 21
