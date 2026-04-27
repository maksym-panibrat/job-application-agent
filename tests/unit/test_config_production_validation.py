import pytest


def _prod_base() -> dict:
    """Minimal valid production settings dict."""
    return {
        "database_url": "postgresql+asyncpg://x:x@localhost/x",
        "google_api_key": "key",
        "jwt_secret": "prod-secret-value-long-enough",
        "cron_shared_secret": "real-cron-secret",
        "google_oauth_client_id": "client-id",
        "google_oauth_client_secret": "client-secret",
        "public_base_url": "https://example.run.app",
        "environment": "production",
    }


def test_valid_production_settings_accepted():
    from app.config import Settings

    s = Settings(**_prod_base())
    assert s.environment == "production"


def test_default_cron_secret_rejected_in_production():
    from app.config import Settings

    data = _prod_base()
    data["cron_shared_secret"] = "dev-cron-secret"
    with pytest.raises(ValueError, match="cron_shared_secret"):
        Settings(**data)


def test_missing_oauth_client_id_rejected_in_production():
    from app.config import Settings

    data = _prod_base()
    data.pop("google_oauth_client_id")
    with pytest.raises(ValueError, match="OAuth"):
        Settings(**data)


def test_missing_oauth_client_secret_rejected_in_production():
    from app.config import Settings

    data = _prod_base()
    data.pop("google_oauth_client_secret")
    with pytest.raises(ValueError, match="OAuth"):
        Settings(**data)


def test_default_jwt_secret_still_rejected_in_production():
    from app.config import Settings

    data = _prod_base()
    data["jwt_secret"] = "dev-secret"
    with pytest.raises(ValueError, match="jwt_secret"):
        Settings(**data)


def test_missing_public_base_url_rejected_in_production():
    from app.config import Settings

    data = _prod_base()
    data.pop("public_base_url")
    with pytest.raises(ValueError, match="public_base_url"):
        Settings(**data)
