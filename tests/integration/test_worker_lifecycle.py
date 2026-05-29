import asyncio

import pytest
from sqlalchemy import text

from app.worker import main as worker_main
from app.worker.queue_service import enqueue


def _disable_default_lanes(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_FAST_JOB_TYPES", "")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")


def test_main_imports_registers_core_handlers():
    from app.worker.handlers import HANDLERS

    assert {
        "fetch-slug",
        "match",
        "batch-match",
        "generate-cover-letter",
        "maintenance",
    }.issubset(set(HANDLERS))


@pytest.mark.asyncio
async def test_worker_processes_pending(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    _disable_default_lanes(monkeypatch)

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
async def test_worker_enqueues_handler_follow_up_after_mark_done(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS, EnqueueAfterDone

    _disable_default_lanes(monkeypatch)

    class FollowUp:
        max_attempts = 3

        async def __call__(self, session, row):
            return EnqueueAfterDone(
                job_type="test-follow",
                payload={"profile_id": "p1"},
                dedupe_key="test-follow:p1",
                not_before_seconds=600,
            )

    monkeypatch.setitem(HANDLERS, "test-follow", FollowUp())

    await enqueue(
        db_session,
        job_type="test-follow",
        payload={"profile_id": "p1"},
        dedupe_key="test-follow:p1",
    )
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    rows = (
        await db_session.execute(
            text(
                "SELECT status, payload, dedupe_key, not_before "
                "FROM work_queue WHERE job_type='test-follow' ORDER BY id"
            )
        )
    ).all()
    assert [row[0] for row in rows] == ["done", "pending"]
    assert rows[1][1] == {"profile_id": "p1"}
    assert rows[1][2] == "test-follow:p1"
    assert rows[1][3] is not None


@pytest.mark.asyncio
async def test_worker_short_circuits_when_attempts_over_cap(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    _disable_default_lanes(monkeypatch)

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
async def test_worker_marks_terminal_failure_when_handler_has_no_hook(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    _disable_default_lanes(monkeypatch)

    class NoTerminalHook:
        max_attempts = 2
        called = 0

        async def __call__(self, session, row):
            NoTerminalHook.called += 1

    monkeypatch.setitem(HANDLERS, "test-no-terminal-hook", NoTerminalHook())

    await enqueue(db_session, job_type="test-no-terminal-hook", payload={})
    await db_session.execute(
        text("UPDATE work_queue SET attempts=3 WHERE job_type='test-no-terminal-hook'")
    )
    await db_session.commit()

    async def stop_soon():
        await asyncio.sleep(1.5)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    row = (
        await db_session.execute(
            text(
                "SELECT status, last_error, not_before "
                "FROM work_queue WHERE job_type='test-no-terminal-hook'"
            )
        )
    ).first()
    assert row[0] == "failed"
    assert "max_attempts" in (row[1] or "")
    assert row[2] is None
    assert NoTerminalHook.called == 0


@pytest.mark.asyncio
async def test_worker_runs_terminal_hook_on_generic_exception(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    _disable_default_lanes(monkeypatch)

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
async def test_worker_releases_unknown_job_type_with_backoff(db_session, monkeypatch):
    _disable_default_lanes(monkeypatch)

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

    _disable_default_lanes(monkeypatch)

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

    _disable_default_lanes(monkeypatch)

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
async def test_worker_lanes_process_default_job_types(db_session, monkeypatch):
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

    monkeypatch.setenv("WORKER_FAST_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "match", Record("fast"))
    monkeypatch.setitem(HANDLERS, "fetch-slug", Record("slow"))

    async def recording_run_lane(lane, *, settings, session_factory, shutdown_task):
        started_lanes.append(lane.name)
        await original_run_lane(
            lane,
            settings=settings,
            session_factory=session_factory,
            shutdown_task=shutdown_task,
        )

    monkeypatch.setattr(worker_main, "_run_lane", recording_run_lane)

    await enqueue(db_session, job_type="match", payload={})
    await enqueue(db_session, job_type="fetch-slug", payload={})
    await db_session.commit()

    async def stop_soon():
        try:
            await asyncio.wait_for(_wait_for_calls(calls, count=2), timeout=2)
        finally:
            worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    assert sorted(started_lanes) == ["fast", "slow"]
    assert sorted(calls) == ["fast", "slow"]


@pytest.mark.asyncio
async def test_slow_lane_drains_while_fast_lane_is_saturated(db_session, monkeypatch):
    started_fast = asyncio.Event()
    release_fast = asyncio.Event()
    slow_done = asyncio.Event()
    from app.worker.handlers import HANDLERS

    class SlowFast:
        max_attempts = 3

        async def __call__(self, session, row):
            started_fast.set()
            await release_fast.wait()

    class FastSlow:
        max_attempts = 3

        async def __call__(self, session, row):
            await started_fast.wait()
            assert not release_fast.is_set()
            slow_done.set()

    monkeypatch.setenv("WORKER_FAST_JOB_TYPES", "test-fast-blocking")
    monkeypatch.setenv("WORKER_FAST_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "test-slow-fast")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "test-fast-blocking", SlowFast())
    monkeypatch.setitem(HANDLERS, "test-slow-fast", FastSlow())

    await enqueue(db_session, job_type="test-fast-blocking", payload={})
    await enqueue(db_session, job_type="test-slow-fast", payload={})
    await db_session.commit()

    async def stop_after_slow_done():
        try:
            await started_fast.wait()
            await asyncio.wait_for(slow_done.wait(), timeout=2)
            assert not release_fast.is_set()
        finally:
            release_fast.set()
            worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_after_slow_done())

    statuses = (
        await db_session.execute(
            text(
                """
                SELECT job_type, status
                FROM work_queue
                WHERE job_type IN ('test-fast-blocking', 'test-slow-fast')
                ORDER BY job_type
                """
            )
        )
    ).all()
    assert statuses == [
        ("test-fast-blocking", "done"),
        ("test-slow-fast", "done"),
    ]


async def _wait_for_calls(calls: list[str], *, count: int) -> None:
    while len(calls) < count:
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_worker_run_cleans_up_sibling_lanes_when_one_lane_raises(monkeypatch):
    started_sibling = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def fake_run_lane(lane, *, settings, session_factory, shutdown_task):
        if lane.name == "fast":
            await started_sibling.wait()
            raise RuntimeError("lane exploded")

        started_sibling.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    class FakeSettings:
        concurrency = 2
        visibility_timeout_s = 30

        def lane_configs(self):
            from app.worker.config import WorkerLane

            return [
                WorkerLane(name="fast", job_types=("test-fast",), concurrency=1),
                WorkerLane(name="slow", job_types=("test-slow",), concurrency=1),
            ]

    monkeypatch.setattr(worker_main, "WorkerSettings", FakeSettings)
    monkeypatch.setattr(worker_main, "get_session_factory", lambda: object())
    monkeypatch.setattr(worker_main, "_run_lane", fake_run_lane)

    with pytest.raises(RuntimeError, match="lane exploded"):
        await asyncio.wait_for(worker_main.run(), timeout=2)

    assert started_sibling.is_set()
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_run_lane_cancels_inflight_after_drain_budget(monkeypatch):
    from app.worker.config import WorkerLane

    handler_started = asyncio.Event()
    handler_cancelled = asyncio.Event()
    claim_count = 0

    class FakeSettings:
        visibility_timeout_s = 30
        poll_interval_s = 60
        drain_budget_s = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def commit(self):
            return None

    class FakeJob:
        id = 1
        job_type = "test-blocking"
        attempts = 1

    async def fake_claim_one(session, *, worker_id, visibility_timeout_s, job_types):
        nonlocal claim_count
        claim_count += 1
        return FakeJob() if claim_count == 1 else None

    async def fake_handle_one(job_row, session_factory, settings, *, lane):
        handler_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise

    monkeypatch.setattr(worker_main, "claim_one", fake_claim_one)
    monkeypatch.setattr(worker_main, "_handle_one", fake_handle_one)

    worker_main._shutdown.clear()
    shutdown_task = asyncio.create_task(worker_main._shutdown.wait())
    lane_task = asyncio.create_task(
        worker_main._run_lane(
            WorkerLane(name="test", job_types=None, concurrency=1),
            settings=FakeSettings(),
            session_factory=FakeSession,
            shutdown_task=shutdown_task,
        )
    )

    try:
        await asyncio.wait_for(handler_started.wait(), timeout=2)
        worker_main._shutdown.set()
        await lane_task
    finally:
        worker_main._shutdown.clear()
        await worker_main._cancel_pending([lane_task, shutdown_task])

    assert handler_cancelled.is_set()
