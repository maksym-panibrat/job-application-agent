"""Daily maintenance handler."""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work_queue import WorkQueue
from app.scheduler.tasks import run_daily_maintenance
from app.worker.handlers import HANDLERS

log = structlog.get_logger()


class MaintenanceHandler:
    max_attempts = 2

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> None:
        result = await run_daily_maintenance()
        await log.ainfo("worker.maintenance.done", **result)


HANDLERS["maintenance"] = MaintenanceHandler()
