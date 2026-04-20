def test_settings_defaults():
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")
    from app.config import Settings

    s = Settings()
    assert s.match_score_threshold == 0.65
    assert s.max_matches_displayed == 20
    assert s.environment == "development"
