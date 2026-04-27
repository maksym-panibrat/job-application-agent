"""Remotive job source adapter.

Remote-only job board. Free public API, no key required.
Non-paginating: always returns all results in one call.
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

REMOTIVE_BASE_URL = "https://remotive.com/api/remote-jobs"

log = structlog.get_logger()


class RemotiveSource(JobSource):
    @property
    def source_name(self) -> str:
        return "remotive"

    @property
    def needs_enrichment(self) -> bool:
        return False

    @property
    def supports_query_cursor(self) -> bool:
        return False

    def _make_cache_key(self, query: str, location: str | None, max_results: int) -> str:
        raw = f"remotive|{query}|{location or ''}|{max_results}"
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
        query: str,
        location: str | None,
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
            query=query,
            location=location,
            results=results,
            expires_at=expires,
        )
        session.add(cache_row)
        await session.commit()

    async def search(
        self,
        query: str,
        location: str | None,
        cursor: Any,
        settings: Any,
        session: AsyncSession,
        *,
        profile: Any = None,
    ) -> tuple[list[JobData], None]:
        max_results = settings.remotive_max_results
        cache_key = self._make_cache_key(query, location, max_results)

        if session is not None:
            cached = await self._get_cached(cache_key, session)
            if cached:
                return self._parse_results(cached, max_results), None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    REMOTIVE_BASE_URL,
                    params={"limit": max_results, "search": query},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            await log.aerror(
                "remotive.fetch_failed",
                source_name="remotive",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return [], None

        if session is not None:
            await self._save_cache(
                cache_key,
                query,
                location,
                data,
                settings.adzuna_cache_ttl_hours,
                session,
            )

        return self._parse_results(data, max_results), None

    def _parse_results(self, data: dict, max_results: int) -> list[JobData]:
        jobs = []
        for item in data.get("jobs", [])[:max_results]:
            apply_url = item.get("url", "")
            if not apply_url:
                continue

            location = item.get("candidate_required_location") or None

            posted_at = None
            if ts := item.get("publication_date"):
                try:
                    posted_at = datetime.fromisoformat(ts)
                except ValueError:
                    pass

            salary = item.get("salary") or None

            jobs.append(
                JobData(
                    external_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company_name=item.get("company_name", ""),
                    location=location,
                    workplace_type="remote",
                    description_md=item.get("description"),
                    salary=salary,
                    contract_type=item.get("job_type"),
                    apply_url=apply_url,
                    posted_at=posted_at,
                )
            )
        return jobs
