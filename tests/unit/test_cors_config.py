def test_cors_allowed_origins_defaults():
    from app.config import Settings

    s = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        google_api_key="key",
    )
    assert "http://localhost:5173" in s.cors_allowed_origins


def test_cors_allowed_origins_overridable(monkeypatch):
    import app.config as cfg_module

    cfg_module._settings = None
    from app.config import Settings

    s = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        google_api_key="key",
        cors_allowed_origins=["https://example.com", "https://app.example.com"],
    )
    assert s.cors_allowed_origins == ["https://example.com", "https://app.example.com"]
    cfg_module._settings = None
