import pytest


class _SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.committed = True


def _session_factory():
    return _SessionContext()


@pytest.mark.asyncio
async def test_terminal_failure_marks_failed_when_handler_has_no_hook(monkeypatch):
    from app.worker import main as worker_main

    calls = []

    async def fake_mark_failed(session, job_id, *, error, worker_id):
        calls.append({"job_id": job_id, "error": error, "worker_id": worker_id})

    monkeypatch.setattr(worker_main, "mark_failed", fake_mark_failed)

    class NoTerminalHook:
        pass

    class JobRow:
        id = 123

    await worker_main._terminal_failure(
        NoTerminalHook(),
        _session_factory,
        JobRow(),
        "boom",
    )

    assert calls == [
        {"job_id": 123, "error": "boom", "worker_id": worker_main._worker_id}
    ]
