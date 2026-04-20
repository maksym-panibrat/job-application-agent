"""
Job sync service — drives the search pipeline:
  1. Generate search queries from profile
  2. For each query × source: check cursor, call source.search(), collect JobData
  3. Dedup by (title, company) — keep best location variant
  4. Enrich each unique job with full description from detail page
  5. Upsert jobs, update cursors, mark stale jobs
"""

import asyncio
from collections import defaultdict

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services import job_service, profile_service
from app.sources.adzuna import AdzunaSource
from app.sources.adzuna_enrichment import fetch_full_description
from app.sources.ats_detection import detect_ats_type, supports_api_apply
from app.sources.base import JobData, JobSource
from app.sources.jsearch import JSearchSource

log = structlog.get_logger()


def generate_queries(profile: UserProfile) -> list[tuple[str, str | None]]:
    """
    Generate (query, location) tuples from profile.

    Location logic:
    - If target_locations is set, cross-product each keyword with each location.
    - If remote_ok and no locations, search without a location (Adzuna returns all).
    - If neither locations nor remote_ok, return empty (profile incomplete).

    Passing "remote" as Adzuna's `where` param produces bad results since `where`
    expects a geographic place name — omit it for remote-only searches instead.

    Returns at most adzuna_max_queries_per_sync tuples.
    """
    settings = get_settings()
    max_q = settings.adzuna_max_queries_per_sync

    keywords = profile.search_keywords or profile.target_roles or []
    # Strip "remote" — it's not a geographic place and confuses Adzuna's where= param
    locations = [loc for loc in (profile.target_locations or []) if loc.lower() != "remote"]

    if not keywords:
        keywords = ["software engineer"]

    queries: list[tuple[str, str | None]] = []

    if locations:
        for kw in keywords:
            for loc in locations:
                queries.append((kw, loc))
                if len(queries) >= max_q:
                    return queries
    elif profile.remote_ok:
        # No geographic restriction — search all locations
        for kw in keywords[:max_q]:
            queries.append((kw, None))
    # else: no locations, not remote_ok → return empty (incomplete profile)

    return queries


def _dedup_jobs(jobs: list[JobData], profile: UserProfile) -> list[JobData]:
    """
    Keep one job per (normalized title, company) group.

    Priority when multiple location variants exist:
    1. Variant explicitly tagged remote (location contains "remote")
    2. Variant whose location matches profile.target_locations[0]
    3. First seen
    """
    groups: dict[tuple[str, str], list[JobData]] = defaultdict(list)
    for j in jobs:
        key = (j.title.lower().strip(), j.company_name.lower().strip())
        groups[key].append(j)

    result: list[JobData] = []
    target_loc = (profile.target_locations[0].lower() if profile.target_locations else "")

    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
            continue

        # Prefer remote variant
        remote = [
            j for j in group
            if (j.workplace_type or "").lower() == "remote"
            or (j.location and "remote" in j.location.lower())
        ]
        if remote:
            result.append(remote[0])
            continue

        # Prefer location match
        if target_loc:
            matched = [j for j in group if j.location and target_loc in j.location.lower()]
            if matched:
                result.append(matched[0])
                continue

        result.append(group[0])

    return result


async def _enrich_jobs(
    jobs: list[JobData],
    existing_full: set[str],
) -> list[JobData]:
    """
    Fetch full descriptions from Adzuna detail pages.

    Skips jobs whose external_id is already in existing_full (already enriched in DB).
    Caps concurrency at 5. Individual failures keep the truncated API description.
    """
    sem = asyncio.Semaphore(5)

    async def enrich_one(j: JobData) -> JobData:
        if j.external_id in existing_full:
            return j
        async with sem:
            try:
                desc, meta, resolved_url = await fetch_full_description(j.apply_url)
                if desc:
                    j.description_md = desc
                if meta:
                    j.salary = meta.get("salary")
                    j.contract_type = meta.get("contract_type")
                # Update apply_url to the resolved destination and re-detect ATS
                if resolved_url and resolved_url != j.apply_url:
                    j.apply_url = resolved_url
                    j.ats_type = detect_ats_type(resolved_url)
                    j.supports_api_apply = supports_api_apply(resolved_url)
            except Exception as exc:
                await log.awarning(
                    "job_sync.enrich_failed",
                    external_id=j.external_id,
                    error=str(exc),
                )
        return j

    return list(await asyncio.gather(*[enrich_one(j) for j in jobs]))


async def _get_already_enriched(
    job_data_list: list[JobData],
    source_name: str,
    session: AsyncSession,
) -> set[str]:
    """
    Return external_ids that are already in the DB with a full description (len > 500).
    These skip the enrichment fetch.
    """
    external_ids = [j.external_id for j in job_data_list]
    if not external_ids:
        return set()

    result = await session.execute(
        select(Job.external_id, Job.description_md).where(
            Job.source == source_name,
            Job.external_id.in_(external_ids),
        )
    )
    rows = result.all()
    return {
        ext_id
        for ext_id, desc in rows
        if desc and len(desc) > 500
    }


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
        sources = []
        if settings.jsearch_api_key.get_secret_value():
            sources.append(JSearchSource())
        if settings.adzuna_app_id and settings.adzuna_api_key.get_secret_value():
            sources.append(AdzunaSource())
        if not sources:
            await log.awarning(
                "job_sync.no_sources_configured",
                profile_id=str(profile.id),
            )
            return {"new_jobs": 0, "updated_jobs": 0, "stale_jobs": 0}

    queries = generate_queries(profile)
    if not queries:
        await log.awarning(
            "job_sync.skipped_incomplete_profile",
            profile_id=str(profile.id),
            reason="no target_locations and remote_ok=False",
        )
        return {"new_jobs": 0, "updated_jobs": 0, "stale_jobs": 0}

    cursors: dict = dict(profile.source_cursors or {})

    total_new = 0
    total_updated = 0

    for source in sources:
        source_name = source.source_name
        source_cursors = cursors.get(source_name, {})

        # Collect all job data for this source across all queries
        all_job_data: list[JobData] = []

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

            all_job_data.extend(job_data_list)
            source_cursors[cursor_key] = next_cursor

        # Dedup by (title, company) across all query results
        unique_jobs = _dedup_jobs(all_job_data, profile)

        # Enrich with full descriptions — skip for sources that already provide them
        if source.needs_enrichment:
            already_enriched = await _get_already_enriched(unique_jobs, source_name, session)
            enriched_jobs = await _enrich_jobs(unique_jobs, already_enriched)
        else:
            enriched_jobs = unique_jobs

        # Upsert
        for job_data in enriched_jobs:
            _, created = await job_service.upsert_job(job_data, source_name, session)
            if created:
                total_new += 1
            else:
                total_updated += 1

        cursors[source_name] = source_cursors

    # Update source_cursors on profile
    await profile_service.update_profile(profile.id, {"source_cursors": cursors}, session)

    # Mark stale jobs
    stale = await job_service.mark_stale_jobs(settings.job_stale_after_days, session)

    result = {
        "new_jobs": total_new,
        "updated_jobs": total_updated,
        "stale_jobs": stale,
        "sources": [s.source_name for s in sources],
    }
    await log.ainfo("job_sync.complete", profile_id=str(profile.id), **result)
    return result
