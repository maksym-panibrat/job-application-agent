import os
import uuid


def setup_env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")


def test_single_user_id_constant():
    setup_env()
    from app.api.deps import SINGLE_USER_ID

    assert SINGLE_USER_ID == uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_settings_auth_disabled_by_default():
    setup_env()
    from app.config import Settings

    s = Settings()
    assert s.auth_enabled is False
