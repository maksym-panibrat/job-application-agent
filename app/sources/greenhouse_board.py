"""Greenhouse board job source adapter.

Fetches all open jobs from Greenhouse company boards directly using the public
boards API. Driven by the user's profile target_company_slugs["greenhouse"] list.
Non-paginating, non-query-driven.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.search_cache import JobSearchCache
from app.sources.base import JobData, JobSource

GREENHOUSE_BOARDS_BASE = "https://boards-api.greenhouse.io/v1/boards"

log = structlog.get_logger()


class GreenhouseBoardSource(JobSource):
    @property
    def source_name(self) -> str:
        return "greenhouse_board"

    @property
    def needs_enrichment(self) -> bool:
        return False

    @property
    def supports_query_cursor(self) -> bool:
        return False

    def _make_cache_key(self, slug: str) -> str:
        raw = f"greenhouse_board|{slug}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get_cached(self, cache_key: str, session: AsyncSession) -> dict | None:
        result = await session.execute(
            select(JobSearchCache).where(
                JobSearchCache.query_hash == cache_key,
                JobSearchCache.expires_at > datetime.now(UTC),
            )
        )
        row = result.scalar_one_or_none()
        return row.results if row else None

    async def _save_cache(
        self,
        cache_key: str,
        slug: str,
        results: dict,
        ttl_hours: int,
        session: AsyncSession,
    ) -> None:
        expires = datetime.now(UTC) + timedelta(hours=ttl_hours)
        existing = await session.execute(
            select(JobSearchCache).where(JobSearchCache.query_hash == cache_key)
        )
        old = existing.scalar_one_or_none()
        if old:
            await session.delete(old)
        cache_row = JobSearchCache(
            source=self.source_name,
            query_hash=cache_key,
            query=slug,
            location=None,
            results=results,
            expires_at=expires,
        )
        session.add(cache_row)
        await session.commit()

    def _parse_job(self, item: dict, slug: str) -> JobData | None:
        job_id = item.get("id")
        title = item.get("title", "")
        apply_url = item.get("absolute_url", "")
        if not apply_url:
            return None

        company_name = slug.replace("-", " ").title()

        location_obj = item.get("location") or {}
        location = location_obj.get("name") or None

        workplace_type = None
        if location and "remote" in location.lower():
            workplace_type = "remote"

        posted_at = None
        if ts := item.get("updated_at"):
            try:
                posted_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return JobData(
            external_id=str(job_id),
            title=title,
            company_name=company_name,
            location=location,
            workplace_type=workplace_type,
            description_md=item.get("content"),
            salary=None,
            contract_type=None,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    async def _fetch_slug(self, slug: str, settings: Any, session: Any) -> list[JobData]:
        cache_key = self._make_cache_key(slug)

        if session is not None:
            cached = await self._get_cached(cache_key, session)
            if cached:
                return [j for item in cached.get("jobs", []) if (j := self._parse_job(item, slug))]

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{GREENHOUSE_BOARDS_BASE}/{slug}/jobs",
                    params={"content": "true"},
                )
                if response.status_code == 404:
                    await log.awarning("greenhouse_board.invalid_slug", slug=slug)
                    return []
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            await log.aerror(
                "greenhouse_board.fetch_failed",
                source_name="greenhouse_board",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return []

        if session is not None:
            await self._save_cache(cache_key, slug, data, settings.adzuna_cache_ttl_hours, session)

        return [j for item in data.get("jobs", []) if (j := self._parse_job(item, slug))]

    async def search(
        self,
        query: str,
        location: str | None,
        cursor: Any,
        settings: Any,
        session: Any,
        *,
        profile: Any = None,
    ) -> tuple[list[JobData], None]:
        if profile is None:
            return [], None

        slugs: list[str] = (profile.target_company_slugs or {}).get("greenhouse", [])
        if not slugs:
            return [], None

        all_jobs: list[JobData] = []
        for slug in slugs:
            try:
                jobs = await self._fetch_slug(slug, settings, session)
                all_jobs.extend(jobs)
            except Exception as exc:
                await log.aerror(
                    "greenhouse_board.fetch_failed",
                    source_name="greenhouse_board",
                    slug=slug,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

        return all_jobs, None
