from fastapi import APIRouter, Depends, Header, HTTPException

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings, get_settings
from app.scheduler.tasks import run_daily_maintenance, run_generation_queue, run_job_sync

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
    try:
        await run_job_sync()
    except BudgetExhausted:
        pass  # non-LLM work (job collection) keeps running
    return {"status": "ok"}


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue():
    try:
        await run_generation_queue()
    except BudgetExhausted:
        pass
    return {"status": "ok"}


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    try:
        await run_daily_maintenance()
    except BudgetExhausted:
        pass
    return {"status": "ok"}
