from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings


class _EmptyScalarResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    def __init__(self, *, execute_error: Exception | None = None):
        self.execute_error = execute_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, stmt):
        if self.execute_error is not None:
            raise self.execute_error
        return _EmptyScalarResult()

    async def commit(self):
        return None


def make_app(
    secret: str = "test-secret",
    raise_server_exceptions: bool = True,
    checkpointer: object | None = object(),
):
    from app.api.internal_cron import get_cron_settings, router

    test_app = FastAPI()
    test_app.include_router(router)

    override_settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        cron_shared_secret=secret,
        google_api_key="fake",
    )
    test_app.dependency_overrides[get_cron_settings] = lambda: override_settings
    # The `checkpointer` parameter is a vestigial parameter kept so existing
    # tests don't break — generate_materials no longer uses a checkpointer
    # (#76 stripped the stale kwarg from the cron path). Set on app.state
    # for any future test that wants to assert state is or isn't present.
    if checkpointer is not None:
        test_app.state.checkpointer = checkpointer
    return TestClient(test_app, raise_server_exceptions=raise_server_exceptions)


def test_sync_missing_secret_returns_403():
    client = make_app()
    resp = client.post("/internal/cron/sync")
    assert resp.status_code == 403


def test_sync_wrong_secret_returns_403():
    client = make_app(secret="real-secret")
    resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 403


def test_sync_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch("app.api.internal_cron.get_session_factory", return_value=lambda: _FakeSession()):
        resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 202
    assert resp.json() == {"enqueued": [], "pruned": 0, "active_profiles": 0}


def test_generation_queue_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch(
        "app.api.internal_cron.run_generation_queue",
        new=AsyncMock(return_value={"attempted": 0, "succeeded": 0, "failed": 0}),
    ) as mock:
        resp = client.post(
            "/internal/cron/generation-queue",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 200
    mock.assert_called_once()


def test_maintenance_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch(
        "app.api.internal_cron.run_daily_maintenance",
        new=AsyncMock(
            return_value={
                "stale_jobs": 0,
                "searches_paused": 0,
                "applications_trimmed": 0,
            }
        ),
    ) as mock:
        resp = client.post("/internal/cron/maintenance", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()


def test_generation_queue_budget_exhausted_returns_structured_response():
    client = make_app(secret="real-secret")
    resumes_at = datetime(2026, 5, 1, tzinfo=UTC)
    with patch(
        "app.api.internal_cron.run_generation_queue",
        new=AsyncMock(side_effect=BudgetExhausted(resumes_at)),
    ):
        resp = client.post(
            "/internal/cron/generation-queue",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "budget_exhausted"
    assert body["resumes_at"] == resumes_at.isoformat()


def test_generation_queue_does_not_require_checkpointer():
    """generate_materials switched to a synchronous (no-checkpointer) contract
    in the cover-letter-graph rewrite. The cron endpoint no longer threads a
    checkpointer; passing one was a stale kwarg that TypeError'd at every call
    and was silently caught by the generic except, masking the bug (#76)."""
    client = make_app(secret="real-secret", checkpointer=None)
    with patch(
        "app.api.internal_cron.run_generation_queue",
        new=AsyncMock(return_value={"attempted": 0, "succeeded": 0, "failed": 0, "deferred": 0}),
    ) as mock:
        resp = client.post(
            "/internal/cron/generation-queue",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 200, resp.text
    mock.assert_called_once()
    # No checkpointer arg passed — call should be plain `run_generation_queue()`.
    call = mock.call_args
    assert call.args == () and call.kwargs == {}, (
        f"run_generation_queue must be called with no args, "
        f"got args={call.args} kwargs={call.kwargs}"
    )


def test_sync_unexpected_exception_returns_500():
    # TestClient with raise_server_exceptions=False mirrors prod behavior: FastAPI
    # converts the unhandled exception into a 500 instead of re-raising into the test.
    # The handler logs the exception with exc_info=True; Cloud Run's stdout capture
    # plus the @type marker in _add_cloud_run_severity are what surface it to GCP
    # Error Reporting — that path is covered separately in test_logging.py.
    client = make_app(secret="real-secret", raise_server_exceptions=False)
    with patch(
        "app.api.internal_cron.get_session_factory",
        return_value=lambda: _FakeSession(
            execute_error=RuntimeError("db connection refused")
        ),
    ):
        resp = client.post(
            "/internal/cron/sync",
            headers={"X-Cron-Secret": "real-secret"},
        )
    assert resp.status_code == 500
