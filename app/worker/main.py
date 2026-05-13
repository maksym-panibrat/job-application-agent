"""Worker entry point for work_queue processing."""

from __future__ import annotations

import asyncio
import random
import signal
import time
import uuid

import structlog

from app.database import get_session_factory
from app.worker.config import WorkerSettings
from app.worker.handlers import (  # noqa: F401
    HANDLERS,
    TransientError,
    fetch_slug,
    generate_cover_letter,
    maintenance,
    match,
)
from app.worker.queue_service import claim_one, mark_done, mark_failed, release_with_backoff

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


async def _handle_one(job_row, session_factory, settings: WorkerSettings) -> None:
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
            job_id=job_row.id,
            job_type=job_row.job_type,
        )
        return

    if job_row.attempts > handler.max_attempts:
        await _terminal_failure(
            handler,
            session_factory,
            job_row,
            error=f"max_attempts ({handler.max_attempts}) exceeded",
        )
        await log.aerror(
            "worker.handler_max_attempts",
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
            job_id=job_row.id,
            job_type=job_row.job_type,
            error=str(exc),
            backoff_s=backoff_s,
        )
        return
    except Exception as exc:
        await _terminal_failure(handler, session_factory, job_row, error=str(exc))
        await log.aexception(
            "worker.job_failed",
            job_id=job_row.id,
            job_type=job_row.job_type,
        )
        return

    async with session_factory() as session:
        await mark_done(session, job_row.id, worker_id=_worker_id)
        await session.commit()
    await log.ainfo(
        "worker.job_done",
        job_id=job_row.id,
        job_type=job_row.job_type,
        worker_id=_worker_id,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )


async def run() -> None:
    settings = WorkerSettings()
    _shutdown.clear()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown.set)
    except (NotImplementedError, RuntimeError, ValueError):
        pass

    inflight: set[asyncio.Task] = set()
    shutdown_task = asyncio.create_task(_shutdown.wait(), name="shutdown-waiter")
    factory = get_session_factory()

    await log.ainfo(
        "worker.started",
        worker_id=_worker_id,
        concurrency=settings.concurrency,
        visibility_timeout_s=settings.visibility_timeout_s,
    )

    while not _shutdown.is_set():
        if len(inflight) >= settings.concurrency:
            await asyncio.wait(
                inflight | {shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            continue

        async with factory() as session:
            job = await claim_one(
                session,
                worker_id=_worker_id,
                visibility_timeout_s=settings.visibility_timeout_s,
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
            job_id=job.id,
            job_type=job.job_type,
            attempts=job.attempts,
            worker_id=_worker_id,
        )
        task = asyncio.create_task(_handle_one(job, factory, settings))
        inflight.add(task)
        task.add_done_callback(inflight.discard)

    await log.ainfo(
        "worker.shutdown_drain_start",
        inflight=len(inflight),
        drain_budget_s=settings.drain_budget_s,
    )
    if inflight:
        await asyncio.wait(inflight, timeout=settings.drain_budget_s)
    await log.ainfo("worker.shutdown_drain_done", inflight=len(inflight))
