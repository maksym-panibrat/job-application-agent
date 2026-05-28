import hmac
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.worker.queue_service import enqueue

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


@router.post(
    "/sync",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cron_sync():
    from app.services import job_sync_service

    factory = get_session_factory()
    async with factory() as session:
        summary = await job_sync_service.sync_active_profiles(session)
    await log.ainfo(
        "cron.sync.completed",
        enqueued=len(summary["enqueued"]),
        pruned=summary["pruned"],
        active_profiles=summary["active_profiles"],
        profiles_enqueued=summary["profiles_enqueued"],
    )
    return summary


@router.post(
    "/generation-reconcile",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cron_generation_reconcile():
    factory = get_session_factory()
    enqueued: list[int] = []
    async with factory() as session:
        orphans = await session.execute(
            text("""
                SELECT a.id::text AS app_id
                FROM applications a
                WHERE a.generation_status IN ('pending')
                  AND a.generation_attempts < 5
                  AND a.updated_at < now() - interval '5 minutes'
                  AND NOT EXISTS (
                      SELECT 1 FROM work_queue w
                      WHERE w.job_type = 'generate-cover-letter'
                        AND w.dedupe_key = 'generate-cover-letter:' || a.id::text
                        AND (
                          w.status IN ('pending', 'in_progress')
                          OR (
                            w.status IN ('done', 'failed')
                            AND w.completed_at > now() - interval '5 minutes'
                          )
                        )
                  )
            """)
        )
        for (app_id,) in orphans.all():
            row_id = await enqueue(
                session,
                job_type="generate-cover-letter",
                payload={"application_id": app_id},
                dedupe_key=f"generate-cover-letter:{app_id}",
            )
            if row_id is not None:
                enqueued.append(row_id)
        await session.commit()
    await log.ainfo("cron.generation_reconcile.completed", reconciled=len(enqueued))
    return {"reconciled": enqueued}


@router.post(
    "/maintenance",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cron_maintenance():
    today = datetime.now(UTC).date().isoformat()
    factory = get_session_factory()
    async with factory() as session:
        row_id = await enqueue(
            session,
            job_type="maintenance",
            payload={"date": today},
            dedupe_key=f"maintenance:{today}",
        )
        await session.commit()
    await log.ainfo("cron.maintenance.completed", enqueued=row_id)
    return {"enqueued": [row_id] if row_id is not None else []}
