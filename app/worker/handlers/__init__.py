"""Handler registry + base Protocol + TransientError.

Concrete handlers live in sibling modules and register themselves via
``HANDLERS[<job_type>] = <singleton>`` on module import. The worker main loop is
responsible for importing those concrete modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work_queue import WorkQueue


class TransientError(Exception):
    def __init__(self, msg: str, *, retry_after_seconds: int | None = None):
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class EnqueueAfterDone:
    job_type: str
    payload: dict[str, Any]
    dedupe_key: str | None = None
    not_before_seconds: int | None = None


class Handler(Protocol):
    max_attempts: int

    async def __call__(
        self,
        session: AsyncSession,
        row: WorkQueue,
    ) -> EnqueueAfterDone | None: ...

    async def on_terminal_failure(
        self,
        session_factory: Any,
        row: WorkQueue,
        error: str,
    ) -> None:
        return None


HANDLERS: dict[str, Handler] = {}
