"""Greenhouse-public-board sync. Single source.

1. From profile.target_company_slugs.greenhouse, fetch jobs via GreenhouseBoardSource.
2. Per-source dedup by (title, company).
3. Upsert. Staleness is owned by run_daily_maintenance, not this path.
"""

import re
import time

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile
from app.services import job_service
from app.sources.base import JobData, JobSource
from app.sources.greenhouse_board import (
    GreenhouseBoardSource,
    InvalidSlugError,
    TransientFetchError,
)

log = structlog.get_logger()


def _normalize(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _dedup(jobs: list[JobData]) -> list[JobData]:
    seen: dict[tuple[str, str], JobData] = {}
    for j in jobs:
        key = (_normalize(j.title), _normalize(j.company_name))
        seen.setdefault(key, j)
    return list(seen.values())


async def sync_profile(
    profile: UserProfile,
    session: AsyncSession,
    sources: list[JobSource] | None = None,
) -> dict:
    """Run a Greenhouse sync for one profile. Returns a summary dict."""
    t0 = time.perf_counter()

    slugs = (profile.target_company_slugs or {}).get("greenhouse", [])
    if not slugs:
        await log.ainfo("job_sync.no_target_slugs", profile_id=str(profile.id))
        return {
            "new_jobs": 0,
            "updated_jobs": 0,
            "stale_jobs": 0,
            "sources": ["greenhouse_board"],
            "warnings": ["no_target_slugs"],
        }

    source = sources[0] if sources else GreenhouseBoardSource()
    source_name = source.source_name
    raw: list[JobData] = []
    failed_slugs: list[dict] = []

    for slug in slugs:
        try:
            jobs, _ = await source.search(query="", location=None, slug=slug)
            raw.extend(jobs)
        except InvalidSlugError as exc:
            failed_slugs.append({"slug": slug, "kind": "invalid", "error": str(exc)})
            await log.awarning(
                "job_sync.invalid_slug", source=source_name, slug=slug, error=str(exc)
            )
        except TransientFetchError as exc:
            failed_slugs.append({"slug": slug, "kind": "transient", "error": str(exc)})
            await log.awarning(
                "job_sync.transient_fetch_error",
                source=source_name,
                slug=slug,
                error=str(exc),
            )
        except Exception as exc:
            failed_slugs.append({"slug": slug, "kind": "error", "error": str(exc)})
            await log.awarning(
                "job_sync.source_error",
                source=source_name,
                slug=slug,
                error=str(exc),
            )

    deduped = _dedup(raw)

    new_count = 0
    updated_count = 0
    for job_data in deduped:
        _, created = await job_service.upsert_job(job_data, source_name, session)
        if created:
            new_count += 1
        else:
            updated_count += 1

    # Staleness is a global, time-based property handled by run_daily_maintenance;
    # running it here once per per-profile sync caused jobs from one profile to
    # flap inactive when another profile synced (issue #49).

    warnings: list[str] = []
    if failed_slugs:
        warnings.append("slug_fetch_errors")

    result = {
        "new_jobs": new_count,
        "updated_jobs": updated_count,
        "stale_jobs": 0,
        "sources": [source_name],
        "warnings": warnings,
        "failed_slugs": failed_slugs,
    }
    await log.ainfo(
        "job_sync.complete",
        profile_id=str(profile.id),
        duration_ms=int((time.perf_counter() - t0) * 1000),
        failed_slug_count=len(failed_slugs),
        **{k: v for k, v in result.items() if k not in ("failed_slugs", "warnings")},
    )
    return result
