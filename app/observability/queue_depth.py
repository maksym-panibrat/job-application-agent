import asyncio

import structlog
from sqlalchemy import text

log = structlog.get_logger()


async def _emit_queue_depth_forever(factory, interval_s: float = 60) -> None:
    """Emit API-side work_queue depth so worker death remains visible."""
    while True:
        try:
            async with factory() as session:
                row = (
                    await session.execute(
                        text("""
                            SELECT
                              count(*) FILTER (WHERE status='pending') AS pending,
                              count(*) FILTER (
                                WHERE status='pending'
                                  AND (not_before IS NULL OR not_before <= now())
                              ) AS eligible_pending,
                              count(*) FILTER (
                                WHERE status='in_progress'
                              ) AS in_progress,
                              EXTRACT(EPOCH FROM (
                                now() - min(enqueued_at)
                                  FILTER (WHERE status='pending')
                              )) AS oldest_pending_age_s,
                              EXTRACT(EPOCH FROM (
                                now() - min(claimed_at)
                                  FILTER (WHERE status='in_progress')
                              )) AS oldest_in_progress_age_s
                            FROM work_queue
                        """)
                    )
                ).first()
                await log.ainfo(
                    "api.queue_depth",
                    pending=int(row.pending),
                    eligible_pending=int(row.eligible_pending),
                    in_progress=int(row.in_progress),
                    oldest_pending_age_s=float(row.oldest_pending_age_s or 0.0),
                    oldest_in_progress_age_s=float(
                        row.oldest_in_progress_age_s or 0.0
                    ),
                )
        except Exception:
            await log.aexception("api.queue_depth_emit_failed")
        await asyncio.sleep(interval_s)
