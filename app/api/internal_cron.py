import time
from collections.abc import Awaitable, Callable

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


async def _run_cron(name: str, task: Callable[[], Awaitable[dict]]) -> dict:
    # Shared handler for the three cron endpoints. Three observable outcomes:
    #   - success                  → {"status": "ok", ...}
    #   - BudgetExhausted          → {"status": "budget_exhausted", "resumes_at": ...}
    #                                (logged + Sentry warning; 200 so the cron runner
    #                                 doesn't alarm — this is expected when monthly
    #                                 Gemini quota hits)
    #   - unexpected exception     → log + explicit Sentry capture + re-raise
    #                                (FastAPI returns 500; the cron runner's own
    #                                 retry/alerting takes over)
    # Explicit capture_exception is deliberate even though Sentry's FastAPI auto-
    # integration catches unhandled 500s — it lets us tag events with the cron
    # name so alerts can fan out by job.
    t0 = time.perf_counter()
    await log.ainfo(f"cron.{name}.started")
    try:
        result = await task()
    except BudgetExhausted as exc:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        resumes_at = exc.resumes_at.isoformat()
        await log.awarning(
            f"cron.{name}.budget_exhausted",
            duration_ms=duration_ms,
            resumes_at=resumes_at,
        )
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("cron.job", name)
            scope.set_tag("cron.outcome", "budget_exhausted")
            sentry_sdk.capture_message(
                f"cron.{name}.budget_exhausted",
                level="warning",
            )
        return {
            "status": "budget_exhausted",
            "duration_ms": duration_ms,
            "resumes_at": resumes_at,
        }
    except Exception as exc:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        await log.aerror(
            f"cron.{name}.failed",
            duration_ms=duration_ms,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("cron.job", name)
            scope.set_tag("cron.outcome", "failed")
            sentry_sdk.capture_exception(exc)
        raise
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo(f"cron.{name}.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/sync", dependencies=[Depends(verify_secret)])
async def cron_sync():
    return await _run_cron("sync", run_job_sync)


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue():
    return await _run_cron("generation_queue", run_generation_queue)


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    return await _run_cron("maintenance", run_daily_maintenance)


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
