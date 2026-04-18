import os


def setup_env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def test_langsmith_defaults(monkeypatch):
    setup_env()
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

    import app.config as cfg
    cfg._settings = None
    from app.config import Settings

    # _env_file=None prevents reading the local .env file so we test pure defaults
    s = Settings(_env_file=None)
    assert s.langsmith_tracing is False
    assert s.langsmith_api_key is None
    assert s.langsmith_project == "job-application-agent"


def test_langsmith_settings_from_env(monkeypatch):
    setup_env()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-project")

    import app.config as cfg
    cfg._settings = None
    from app.config import Settings

    s = Settings()
    assert s.langsmith_tracing is True
    assert s.langsmith_api_key is not None
    assert s.langsmith_api_key.get_secret_value() == "lsv2_test_key"
    assert s.langsmith_project == "my-project"
