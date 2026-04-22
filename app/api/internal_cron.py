import hmac
import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request

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
    if x_cron_secret is None or not hmac.compare_digest(x_cron_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid cron secret")


async def _run_cron(name: str, task: Callable[[], Awaitable[dict]]) -> dict:
    # Shared handler for the three cron endpoints. Three observable outcomes:
    #   - success             → 200 {"status": "ok", ...}
    #   - BudgetExhausted     → 200 {"status": "budget_exhausted", "resumes_at": ...}
    #                           (warn log; 200 so the cron runner doesn't alarm — this
    #                            is expected when monthly Gemini quota hits)
    #   - unexpected exception → structured error log with exc_info=True + re-raise
    #                            (FastAPI returns 500; Cloud Run stdout log + GCP Cloud
    #                             Error Reporting pick it up via severity=ERROR + @type)
    t0 = time.perf_counter()
    await log.ainfo(f"cron.{name}.started")
    try:
        result = await task()
    except BudgetExhausted as exc:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        resumes_at = exc.resumes_at.isoformat()
        await log.awarning(
            f"cron.{name}.budget_exhausted",
            cron_job=name,
            duration_ms=duration_ms,
            resumes_at=resumes_at,
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
            cron_job=name,
            duration_ms=duration_ms,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo(f"cron.{name}.completed", cron_job=name, duration_ms=duration_ms, **result)
    # Spread task result first so the handler-level contract keys (status, duration_ms)
    # always win if a task ever starts returning a key with the same name.
    return {**result, "status": "ok", "duration_ms": duration_ms}


@router.post("/sync", dependencies=[Depends(verify_secret)])
async def cron_sync():
    return await _run_cron("sync", run_job_sync)


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue(request: Request):
    # generate_materials requires a LangGraph checkpointer; resolve it from the
    # app state (initialized in the FastAPI lifespan) and 503 loudly if missing.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="checkpointer not initialized")

    async def task() -> dict:
        return await run_generation_queue(checkpointer)

    return await _run_cron("generation_queue", task)


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    return await _run_cron("maintenance", run_daily_maintenance)
