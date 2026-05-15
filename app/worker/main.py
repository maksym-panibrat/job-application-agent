"""Worker entry point for work_queue processing."""

from __future__ import annotations

import asyncio
import random
import signal
import time
import uuid

import structlog

from app.database import get_session_factory
from app.worker.config import WorkerLane, WorkerSettings
from app.worker.handlers import (  # noqa: F401
    HANDLERS,
    TransientError,
    fetch_slug,
    generate_cover_letter,
    maintenance,
    match,
)
from app.worker.queue_service import StaleLease, claim_one, mark_failed, release_with_backoff

log = structlog.get_logger()
_worker_id = str(uuid.uuid4())


class _LoopLocalEvent:
    def __init__(self) -> None:
        self._events: dict[asyncio.AbstractEventLoop, asyncio.Event] = {}

    def _event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        event = self._events.get(loop)
        if event is None:
            event = asyncio.Event()
            self._events[loop] = event
        return event

    def clear(self) -> None:
        self._event().clear()

    def set(self) -> None:
        self._event().set()

    def is_set(self) -> bool:
        return self._event().is_set()

    async def wait(self) -> bool:
        return await self._event().wait()


_shutdown = _LoopLocalEvent()


def _compute_backoff(attempts: int, settings: WorkerSettings) -> int:
    base = settings.transient_backoff_base_s
    raw = base * (2 ** max(attempts - 1, 0)) + random.uniform(0, base / 2)
    return min(int(raw), settings.transient_backoff_max_s)


async def _terminal_failure(handler, session_factory, job_row, error: str) -> None:
    await handler.on_terminal_failure(session_factory, job_row, error)
    async with session_factory() as session:
        await mark_failed(session, job_row.id, error=error, worker_id=_worker_id)
        await session.commit()


async def _release_for_retry(session_factory, job_row, seconds: int) -> None:
    async with session_factory() as session:
        await release_with_backoff(
            session,
            job_row.id,
            seconds=seconds,
            worker_id=_worker_id,
        )
        await session.commit()


async def _handle_one(
    job_row,
    session_factory,
    settings: WorkerSettings,
    *,
    lane: str,
) -> None:
    handler = HANDLERS.get(job_row.job_type)
    if handler is None:
        async with session_factory() as session:
            await release_with_backoff(
                session,
                job_row.id,
                seconds=settings.unknown_type_backoff_s,
                worker_id=_worker_id,
            )
            await session.commit()
        await log.awarning(
            "worker.unknown_job_type",
            lane=lane,
            job_id=job_row.id,
            job_type=job_row.job_type,
        )
        return

    if job_row.attempts > handler.max_attempts:
        try:
            await _terminal_failure(
                handler,
                session_factory,
                job_row,
                error=f"max_attempts ({handler.max_attempts}) exceeded",
            )
        except Exception:
            await log.aexception(
                "worker.terminal_failure_hook_retry",
                lane=lane,
                job_id=job_row.id,
                job_type=job_row.job_type,
            )
            try:
                await _release_for_retry(
                    session_factory,
                    job_row,
                    settings.transient_backoff_max_s,
                )
            except StaleLease:
                pass
        else:
            await log.aerror(
                "worker.handler_max_attempts",
                lane=lane,
                job_id=job_row.id,
                job_type=job_row.job_type,
                attempts=job_row.attempts,
                max_attempts=handler.max_attempts,
            )
        return

    started_at = time.monotonic()
    try:
        async with session_factory() as session:
            await handler(session, job_row)
            await session.commit()
    except TransientError as exc:
        backoff_s = exc.retry_after_seconds or _compute_backoff(job_row.attempts, settings)
        async with session_factory() as session:
            await release_with_backoff(
                session,
                job_row.id,
                seconds=backoff_s,
                worker_id=_worker_id,
            )
            await session.commit()
        await log.awarning(
            "worker.transient_failure",
            lane=lane,
            job_id=job_row.id,
            job_type=job_row.job_type,
            error=str(exc),
            backoff_s=backoff_s,
        )
        return
    except Exception as exc:
        try:
            await _terminal_failure(handler, session_factory, job_row, error=str(exc))
        except Exception:
            await log.aexception(
                "worker.terminal_failure_hook_retry",
                lane=lane,
                job_id=job_row.id,
                job_type=job_row.job_type,
            )
            try:
                await _release_for_retry(
                    session_factory,
                    job_row,
                    settings.transient_backoff_max_s,
                )
            except StaleLease:
                pass
        else:
            await log.aexception(
                "worker.job_failed",
                lane=lane,
                job_id=job_row.id,
                job_type=job_row.job_type,
            )
        return

    try:
        async with session_factory() as session:
            from app.worker import queue_service

            await queue_service.mark_done(session, job_row.id, worker_id=_worker_id)
            await session.commit()
    except Exception:
        await log.aexception(
            "worker.mark_done_failed",
            lane=lane,
            job_id=job_row.id,
            job_type=job_row.job_type,
        )
        try:
            await _release_for_retry(
                session_factory,
                job_row,
                settings.mark_done_retry_backoff_s,
            )
        except Exception:
            await log.aexception(
                "worker.mark_done_release_failed",
                lane=lane,
                job_id=job_row.id,
                job_type=job_row.job_type,
            )
        return
    else:
        await log.ainfo(
            "worker.job_done",
            lane=lane,
            job_id=job_row.id,
            job_type=job_row.job_type,
            worker_id=_worker_id,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )


async def _run_lane(
    lane: WorkerLane,
    *,
    settings: WorkerSettings,
    session_factory,
    shutdown_task: asyncio.Task,
) -> None:
    inflight: set[asyncio.Task] = set()

    while not _shutdown.is_set():
        if len(inflight) >= lane.concurrency:
            await asyncio.wait(
                inflight | {shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            continue

        async with session_factory() as session:
            job = await claim_one(
                session,
                worker_id=_worker_id,
                visibility_timeout_s=settings.visibility_timeout_s,
                job_types=lane.job_types,
            )
            await session.commit()

        if job is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(shutdown_task),
                    timeout=settings.poll_interval_s,
                )
            except TimeoutError:
                pass
            continue

        await log.ainfo(
            "worker.job_start",
            lane=lane.name,
            job_id=job.id,
            job_type=job.job_type,
            attempts=job.attempts,
            worker_id=_worker_id,
        )
        task = asyncio.create_task(
            _handle_one(job, session_factory, settings, lane=lane.name)
        )
        inflight.add(task)
        task.add_done_callback(inflight.discard)

    await log.ainfo(
        "worker.shutdown_drain_start",
        lane=lane.name,
        inflight=len(inflight),
        drain_budget_s=settings.drain_budget_s,
    )
    if inflight:
        await asyncio.wait(inflight, timeout=settings.drain_budget_s)
    await log.ainfo(
        "worker.shutdown_drain_done",
        lane=lane.name,
        inflight=len(inflight),
    )


async def run() -> None:
    settings = WorkerSettings()
    lanes = settings.lane_configs()
    _shutdown.clear()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown.set)
    except (NotImplementedError, RuntimeError, ValueError):
        pass

    shutdown_task = asyncio.create_task(_shutdown.wait(), name="shutdown-waiter")
    factory = get_session_factory()

    await log.ainfo(
        "worker.started",
        worker_id=_worker_id,
        concurrency=settings.concurrency,
        lanes=[
            {
                "name": lane.name,
                "concurrency": lane.concurrency,
                "job_types": lane.job_types,
            }
            for lane in lanes
        ],
        visibility_timeout_s=settings.visibility_timeout_s,
    )

    lane_tasks = [
        asyncio.create_task(
            _run_lane(
                lane,
                settings=settings,
                session_factory=factory,
                shutdown_task=shutdown_task,
            ),
            name=f"worker-lane-{lane.name}",
        )
        for lane in lanes
    ]
    await asyncio.gather(*lane_tasks)
