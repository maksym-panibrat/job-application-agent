import hmac
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlmodel import select

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models.user_profile import UserProfile
from app.worker.payloads import FetchSlugPayload
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


@router.post(
    "/sync",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cron_sync():
    from app.services import slug_registry_service

    factory = get_session_factory()
    enqueued: list[int] = []
    pruned_total = 0
    active_count = 0
    async with factory() as session:
        active_profiles = (
            (
                await session.execute(
                    select(UserProfile).where(UserProfile.search_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        active_count = len(active_profiles)
        for profile in active_profiles:
            pruned_total += await slug_registry_service.prune_invalid_for_profile(
                profile,
                session,
            )
            stale = await slug_registry_service.list_stale_for_profile(
                profile,
                session,
                ttl_hours=6,
            )
            for provider, slug in stale:
                row_id = await enqueue(
                    session,
                    job_type="fetch-slug",
                    payload=FetchSlugPayload(provider=provider, slug=slug).model_dump(),
                    dedupe_key=f"fetch-slug:{provider}:{slug}",
                )
                if row_id is not None:
                    enqueued.append(row_id)
            profile.last_sync_requested_at = datetime.now(UTC)
            profile.last_sync_summary = {
                "queued_slugs": [slug for _, slug in stale],
                "matched_now": 0,
                "pruned_slugs": pruned_total,
            }
            session.add(profile)
        await session.commit()
    await log.ainfo(
        "cron.sync.completed",
        enqueued=len(enqueued),
        pruned=pruned_total,
        active_profiles=active_count,
    )
    return {
        "enqueued": enqueued,
        "pruned": pruned_total,
        "active_profiles": active_count,
    }


@router.post(
    "/generation-queue",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def cron_generation_queue():
    await log.ainfo("cron.deprecated_endpoint_hit", endpoint="generation-queue")
    return {
        "status": "deprecated",
        "note": "use /internal/cron/generation-reconcile + inline-enqueue",
    }


@router.post(
    "/process-sync-queue",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def cron_process_sync_queue():
    await log.ainfo("cron.deprecated_endpoint_hit", endpoint="process-sync-queue")
    return {
        "status": "deprecated",
        "note": "worker drains internally; this shim is a no-op",
    }


@router.post(
    "/process-match-queue",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def cron_process_match_queue():
    await log.ainfo("cron.deprecated_endpoint_hit", endpoint="process-match-queue")
    return {
        "status": "deprecated",
        "note": "worker drains internally; this shim is a no-op",
    }


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
                WHERE a.generation_status = 'pending'
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
    "/post-cutover-match-reconcile",
    dependencies=[Depends(verify_secret)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cron_post_cutover_match_reconcile():
    factory = get_session_factory()
    enqueued: list[int] = []
    async with factory() as session:
        orphans = await session.execute(
            text("""
                SELECT a.id::text AS app_id
                FROM applications a
                WHERE a.match_status = 'pending_match'
                  AND a.updated_at < now() - interval '5 minutes'
                  AND NOT EXISTS (
                      SELECT 1 FROM work_queue w
                      WHERE w.job_type = 'match'
                        AND w.dedupe_key = 'match:' || a.id::text
                        AND w.status IN ('pending', 'in_progress')
                  )
            """)
        )
        for (app_id,) in orphans.all():
            row_id = await enqueue(
                session,
                job_type="match",
                payload={"application_id": app_id},
                dedupe_key=f"match:{app_id}",
            )
            if row_id is not None:
                enqueued.append(row_id)
        await session.commit()
    await log.ainfo(
        "cron.post_cutover_match_reconcile.completed",
        reconciled=len(enqueued),
    )
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
