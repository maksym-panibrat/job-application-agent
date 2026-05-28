import pytest


def _reset_settings() -> None:
    import app.config as cfg

    cfg._settings = None


def test_get_batch_match_provider_returns_fake_in_test_environment(monkeypatch):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        get_batch_match_provider,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("BATCH_MATCH_PROVIDER", "gemini")
    monkeypatch.setenv("BATCH_MATCH_DRY_RUN", "false")
    _reset_settings()

    try:
        provider = get_batch_match_provider()
    finally:
        _reset_settings()

    assert isinstance(provider, FakeBatchMatchProvider)
    assert provider.ready is False


def test_get_batch_match_provider_returns_fake_when_dry_run_enabled(monkeypatch):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        get_batch_match_provider,
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BATCH_MATCH_PROVIDER", "gemini")
    monkeypatch.setenv("BATCH_MATCH_DRY_RUN", "true")
    _reset_settings()

    try:
        provider = get_batch_match_provider()
    finally:
        _reset_settings()

    assert isinstance(provider, FakeBatchMatchProvider)
    assert provider.ready is False


def test_get_batch_match_provider_rejects_unknown_provider(monkeypatch):
    from app.services.batch_match_provider import get_batch_match_provider

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BATCH_MATCH_PROVIDER", "bogus")
    monkeypatch.setenv("BATCH_MATCH_DRY_RUN", "false")
    _reset_settings()

    try:
        with pytest.raises(ValueError, match="unknown batch match provider: bogus"):
            get_batch_match_provider()
    finally:
        _reset_settings()


def test_get_batch_match_provider_rejects_gemini_until_implemented(monkeypatch):
    from app.services.batch_match_provider import get_batch_match_provider

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BATCH_MATCH_PROVIDER", "gemini")
    monkeypatch.setenv("BATCH_MATCH_DRY_RUN", "false")
    _reset_settings()

    try:
        with pytest.raises(
            ValueError,
            match="gemini batch match provider is not implemented",
        ):
            get_batch_match_provider()
    finally:
        _reset_settings()
