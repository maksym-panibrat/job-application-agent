import sys

from fastapi.testclient import TestClient


def test_test_helpers_not_mounted_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-value-long-enough")
    monkeypatch.setenv("CRON_SHARED_SECRET", "real-cron-secret")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    import app.config as cfg_module
    cfg_module._settings = None

    # Remove cached app.main so it re-evaluates the conditional router mount
    for mod in list(sys.modules.keys()):
        if mod == "app.main" or mod.startswith("app.main."):
            del sys.modules[mod]

    import app.main as main_module
    client = TestClient(main_module.app, raise_server_exceptions=False)

    # Verify test_helpers routes are absent from the router (not just blocked at HTTP level)
    all_paths = [getattr(r, "path", "") for r in main_module.app.routes]
    assert not any("/api/test" in p for p in all_paths), (
        f"test_helpers router mounted in production: {[p for p in all_paths if '/api/test' in p]}"
    )
    # POST should not succeed regardless of static file handling
    resp = client.post("/api/test/seed")
    assert resp.status_code not in (200, 201, 422), (
        f"test endpoint active in production: {resp.status_code}"
    )

    cfg_module._settings = None
    # Re-remove app.main so subsequent tests get a clean import
    for mod in list(sys.modules.keys()):
        if mod == "app.main" or mod.startswith("app.main."):
            del sys.modules[mod]
