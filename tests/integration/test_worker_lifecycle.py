import asyncio

import pytest
from sqlalchemy import text

from app.worker import main as worker_main
from app.worker.queue_service import enqueue


def test_main_imports_register_all_four_handlers():
    from app.worker.handlers import HANDLERS

    assert set(HANDLERS) == {
        "fetch-slug",
        "match",
        "generate-cover-letter",
        "maintenance",
    }


@pytest.mark.asyncio
async def test_worker_processes_pending(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    class Noop:
        max_attempts = 3
        called = 0

        async def __call__(self, session, row):
            Noop.called += 1

    monkeypatch.setitem(HANDLERS, "test-noop", Noop())

    for _ in range(5):
        await enqueue(db_session, job_type="test-noop", payload={})
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(2.0)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM work_queue "
                "WHERE status='done' AND job_type='test-noop'"
            )
        )
    ).scalar_one()
    assert count == 5
    assert Noop.called == 5


@pytest.mark.asyncio
async def test_worker_short_circuits_when_attempts_over_cap(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    class Capped:
        max_attempts = 2
        called = 0
        terminal_hook_called = False

        async def __call__(self, session, row):
            Capped.called += 1

        async def on_terminal_failure(self, session_factory, row, error):
            Capped.terminal_hook_called = True

    monkeypatch.setitem(HANDLERS, "test-cap", Capped())

    await enqueue(db_session, job_type="test-cap", payload={})
    await db_session.execute(
        text("UPDATE work_queue SET attempts=3 WHERE job_type='test-cap'")
    )
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    row = (
        await db_session.execute(
            text("SELECT status, last_error FROM work_queue WHERE job_type='test-cap'")
        )
    ).first()
    assert row[0] == "failed"
    assert "max_attempts" in (row[1] or "")
    assert Capped.called == 0
    assert Capped.terminal_hook_called


@pytest.mark.asyncio
async def test_worker_runs_terminal_hook_on_generic_exception(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    class Bomb:
        max_attempts = 3
        terminal_hook_called = False

        async def __call__(self, session, row):
            raise RuntimeError("boom")

        async def on_terminal_failure(self, session_factory, row, error):
            Bomb.terminal_hook_called = True

    monkeypatch.setitem(HANDLERS, "test-bomb", Bomb())

    await enqueue(db_session, job_type="test-bomb", payload={})
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    status = (
        await db_session.execute(
            text("SELECT status FROM work_queue WHERE job_type='test-bomb'")
        )
    ).scalar_one()
    assert status == "failed"
    assert Bomb.terminal_hook_called


@pytest.mark.asyncio
async def test_worker_releases_unknown_job_type_with_backoff(db_session):
    await enqueue(db_session, job_type="future-unknown-type", payload={})
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    row = (
        await db_session.execute(
            text(
                "SELECT status, attempts, not_before FROM work_queue "
                "WHERE job_type='future-unknown-type'"
            )
        )
    ).first()
    assert row[0] == "pending"
    assert row[1] == 1
    assert row[2] is not None


@pytest.mark.asyncio
async def test_worker_drains_in_flight_on_shutdown(db_session, monkeypatch):
    started = asyncio.Event()
    finished = asyncio.Event()
    from app.worker.handlers import HANDLERS

    class Slow:
        max_attempts = 3

        async def __call__(self, session, row):
            started.set()
            await asyncio.sleep(0.5)
            finished.set()

    monkeypatch.setitem(HANDLERS, "test-slow", Slow())

    await enqueue(db_session, job_type="test-slow", payload={})
    await db_session.commit()

    async def stop_when_started():
        await started.wait()
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_when_started())

    assert finished.is_set()
    status = (
        await db_session.execute(
            text("SELECT status FROM work_queue WHERE job_type='test-slow'")
        )
    ).scalar_one()
    assert status == "done"


@pytest.mark.asyncio
async def test_mark_done_failure_releases_row_for_replay(db_session, monkeypatch):
    from app.worker import queue_service
    from app.worker.handlers import HANDLERS

    class Succeed:
        max_attempts = 3

        async def __call__(self, session, row):
            pass

    monkeypatch.setitem(HANDLERS, "test-domain-ok", Succeed())

    await enqueue(db_session, job_type="test-domain-ok", payload={})
    await db_session.commit()

    async def failing_mark_done(session, job_id, *, worker_id):
        raise RuntimeError("simulated mark_done DB failure")

    monkeypatch.setattr(queue_service, "mark_done", failing_mark_done)
    monkeypatch.setattr(worker_main, "mark_done", failing_mark_done, raising=False)

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    row = (
        await db_session.execute(
            text(
                "SELECT status, last_error, claimed_by, not_before FROM work_queue "
                "WHERE job_type='test-domain-ok'"
            )
        )
    ).first()
    assert row[0] == "pending"
    assert row[1] is None
    assert row[2] is None
    assert row[3] is not None


@pytest.mark.asyncio
async def test_worker_lanes_process_allowed_job_types(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    calls: list[str] = []
    started_lanes: list[str] = []
    original_run_lane = worker_main._run_lane

    class Record:
        max_attempts = 3

        def __init__(self, name: str) -> None:
            self.name = name

        async def __call__(self, session, row):
            calls.append(self.name)

    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", "test-llm")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "test-slow")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "test-llm", Record("llm"))
    monkeypatch.setitem(HANDLERS, "test-slow", Record("slow"))

    async def recording_run_lane(lane, *, settings, session_factory, shutdown_task):
        started_lanes.append(lane.name)
        await original_run_lane(
            lane,
            settings=settings,
            session_factory=session_factory,
            shutdown_task=shutdown_task,
        )

    monkeypatch.setattr(worker_main, "_run_lane", recording_run_lane)

    await enqueue(db_session, job_type="test-llm", payload={})
    await enqueue(db_session, job_type="test-slow", payload={})
    await db_session.commit()

    async def stop_soon():
        while len(calls) < 2:
            await asyncio.sleep(0.05)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    assert sorted(started_lanes) == ["llm", "slow"]
    assert sorted(calls) == ["llm", "slow"]


@pytest.mark.asyncio
async def test_slow_lane_drains_while_llm_lane_is_saturated(db_session, monkeypatch):
    started_llm = asyncio.Event()
    release_llm = asyncio.Event()
    slow_done = asyncio.Event()
    from app.worker.handlers import HANDLERS

    class SlowLlm:
        max_attempts = 3

        async def __call__(self, session, row):
            started_llm.set()
            await release_llm.wait()

    class FastSlow:
        max_attempts = 3

        async def __call__(self, session, row):
            await started_llm.wait()
            assert not release_llm.is_set()
            slow_done.set()

    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", "test-llm-blocking")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "test-slow-fast")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "test-llm-blocking", SlowLlm())
    monkeypatch.setitem(HANDLERS, "test-slow-fast", FastSlow())

    await enqueue(db_session, job_type="test-llm-blocking", payload={})
    await enqueue(db_session, job_type="test-slow-fast", payload={})
    await db_session.commit()

    async def stop_after_slow_done():
        try:
            await started_llm.wait()
            await asyncio.wait_for(slow_done.wait(), timeout=2)
            assert not release_llm.is_set()
        finally:
            release_llm.set()
            worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_after_slow_done())

    statuses = (
        await db_session.execute(
            text(
                """
                SELECT job_type, status
                FROM work_queue
                WHERE job_type IN ('test-llm-blocking', 'test-slow-fast')
                ORDER BY job_type
                """
            )
        )
    ).all()
    assert statuses == [
        ("test-llm-blocking", "done"),
        ("test-slow-fast", "done"),
    ]
