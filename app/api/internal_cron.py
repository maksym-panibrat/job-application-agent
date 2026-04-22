import time

import sentry_sdk
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings, get_settings
from app.scheduler.tasks import run_daily_maintenance, run_generation_queue, run_job_sync

log = structlog.get_logger()
router = APIRouter(prefix="/internal/cron", tags=["cron"])


def get_cron_settings() -> Settings:
    return get_settings()


async def verify_secret(
    x_cron_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_cron_settings),
) -> None:
    expected = settings.cron_shared_secret.get_secret_value()
    if x_cron_secret is None or x_cron_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.post("/sync", dependencies=[Depends(verify_secret)])
async def cron_sync():
    t0 = time.perf_counter()
    await log.ainfo("cron.sync.started")
    result: dict = {}
    try:
        result = await run_job_sync()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.sync.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue():
    t0 = time.perf_counter()
    await log.ainfo("cron.generation_queue.started")
    result: dict = {}
    try:
        result = await run_generation_queue()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.generation_queue.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    t0 = time.perf_counter()
    await log.ainfo("cron.maintenance.started")
    result: dict = {}
    try:
        result = await run_daily_maintenance()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.maintenance.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/sentry-ping", dependencies=[Depends(verify_secret)])
async def sentry_ping(settings: Settings = Depends(get_cron_settings)):
    # Sends a deliberate Sentry event so operators can verify DSN + release-tag wiring
    # end-to-end against the deployed app. Returns {"sent": false} if Sentry is disabled
    # so CI can distinguish "no DSN configured" from "DSN configured but broken".
    if not settings.sentry_dsn:
        await log.ainfo("sentry.ping.skipped", reason="no_dsn_configured")
        return {"sent": False, "reason": "no_dsn_configured"}
    event_id = sentry_sdk.capture_message(
        "sentry-ping: smoke verification",
        level="info",
    )
    await log.ainfo(
        "sentry.ping.sent",
        event_id=event_id,
        release=settings.sentry_release,
    )
    return {"sent": True, "event_id": event_id, "release": settings.sentry_release}
