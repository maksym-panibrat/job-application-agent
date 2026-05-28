def test_settings_defaults():
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
    from app.config import Settings

    s = Settings()
    assert s.match_score_threshold == 0.65


def test_job_stale_after_days_default_is_21():
    """Stale TTL bumped from 14d to 21d (spec 2026-04-28)."""
    from app.config import Settings

    s = Settings(database_url="postgresql://x:x@x/x")
    assert s.job_stale_after_days == 21


def test_queue_depth_emit_interval_can_be_configured(monkeypatch):
    from app import config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("QUEUE_DEPTH_EMIT_INTERVAL_S", "17")
    config._settings = None

    try:
        settings = config.get_settings()
        assert settings.queue_depth_emit_interval_s == 17
    finally:
        config._settings = None
