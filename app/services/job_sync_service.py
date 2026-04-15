"""
Job sync service — drives the search pipeline:
  1. Generate search queries from profile
  2. For each query × source: check cursor, call source.search(), upsert jobs
  3. Update source_cursors in profile
  4. Mark stale jobs
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user_profile import UserProfile
from app.services import job_service, profile_service
from app.sources.adzuna import AdzunaSource
from app.sources.base import JobSource

log = structlog.get_logger()


def generate_queries(profile: UserProfile) -> list[tuple[str, str | None]]:
    """
    Generate (query, location) tuples from profile.
    Returns at most adzuna_max_queries_per_sync tuples.
    """
    settings = get_settings()
    max_q = settings.adzuna_max_queries_per_sync
    queries = []

    # Use explicit search_keywords if set, otherwise fall back to target_roles
    keywords = profile.search_keywords or profile.target_roles or []
    locations = profile.target_locations or []

    for kw in keywords[:max_q]:
        location = locations[0] if locations else None
        if profile.remote_ok and not location:
            location = "remote"
        queries.append((kw, location))
        if len(queries) >= max_q:
            break

    if not queries:
        queries = [("software engineer", None)]

    return queries


async def sync_profile(
    profile: UserProfile,
    session: AsyncSession,
    sources: list[JobSource] | None = None,
) -> dict:
    """
    Run a full sync for one profile. Returns a summary dict.
    """
    settings = get_settings()
    if sources is None:
        sources = [AdzunaSource()]

    queries = generate_queries(profile)
    cursors: dict = dict(profile.source_cursors or {})

    total_new = 0
    total_updated = 0
    jobs_seen = []

    for source in sources:
        source_name = source.source_name
        source_cursors = cursors.get(source_name, {})

        for query, location in queries:
            cursor_key = f"{query}|{location or ''}"
            cursor = source_cursors.get(cursor_key, 1)

            try:
                job_data_list, next_cursor = await source.search(
                    query, location, cursor, settings, session
                )
            except Exception as exc:
                await log.awarning(
                    "job_sync.source_error",
                    source=source_name,
                    query=query,
                    error=str(exc),
                )
                continue

            for job_data in job_data_list:
                job, created = await job_service.upsert_job(job_data, source_name, session)
                jobs_seen.append(job.id)
                if created:
                    total_new += 1
                else:
                    total_updated += 1

            source_cursors[cursor_key] = next_cursor

        cursors[source_name] = source_cursors

    # Update source_cursors on profile
    await profile_service.update_profile(profile.id, {"source_cursors": cursors}, session)

    # Mark stale jobs
    stale = await job_service.mark_stale_jobs(settings.job_stale_after_days, session)

    result = {
        "new_jobs": total_new,
        "updated_jobs": total_updated,
        "stale_jobs": stale,
    }
    await log.ainfo("job_sync.complete", profile_id=str(profile.id), **result)
    return result
