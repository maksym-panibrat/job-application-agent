"""Arbeitnow job source adapter.

Free public API, no auth required. Paginated by integer page cursor.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.search_cache import JobSearchCache
from app.sources.ats_detection import detect_ats_type
from app.sources.ats_detection import supports_api_apply as ats_supports_api_apply
from app.sources.base import JobData, JobSource

ARBEITNOW_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"

log = structlog.get_logger()


class ArbeitnowSource(JobSource):
    @property
    def source_name(self) -> str:
        return "arbeitnow"

    @property
    def needs_enrichment(self) -> bool:
        return False

    @property
    def supports_query_cursor(self) -> bool:
        return True

    def _make_cache_key(self, query: str, location: str | None, page: int) -> str:
        return f"arbeitnow|{query}|{location or ''}|{page}"

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
        session: Any,
        *,
        profile: Any = None,
    ) -> tuple[list[JobData], Any]:
        page = cursor if isinstance(cursor, int) and cursor > 0 else 1
        cache_key = self._make_cache_key(query, location, page)

        if session is not None:
            cached = await self._get_cached(cache_key, session)
            if cached:
                jobs = self._parse_results(cached, query)
                return jobs, page + 1 if jobs else None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(ARBEITNOW_BASE_URL, params={"page": page})
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            await log.awarning("arbeitnow.fetch_failed", error=str(exc))
            return [], None

        if not data.get("data"):
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

        jobs = self._parse_results(data, query)
        return jobs, page + 1

    def _parse_results(self, data: dict, query: str) -> list[JobData]:
        tokens = query.lower().split() if query.strip() else []
        jobs = []

        for item in data.get("data", []):
            title = item.get("title", "")
            if tokens and not any(t in title.lower() for t in tokens):
                continue

            apply_url = item.get("url", "")
            if not apply_url:
                continue

            ats = detect_ats_type(apply_url)
            api_apply = ats_supports_api_apply(apply_url)

            workplace_type = "remote" if item.get("remote") else None

            job_types = item.get("job_types") or []
            contract_type = " / ".join(job_types) if job_types else None

            posted_at = None
            if created_at := item.get("created_at"):
                try:
                    posted_at = datetime.fromtimestamp(created_at, tz=UTC)
                except (ValueError, OSError, OverflowError):
                    pass

            jobs.append(
                JobData(
                    external_id=item.get("slug", ""),
                    title=title,
                    company_name=item.get("company_name", ""),
                    location=item.get("location") or None,
                    workplace_type=workplace_type,
                    description_md=item.get("description"),
                    salary=None,
                    contract_type=contract_type,
                    apply_url=apply_url,
                    posted_at=posted_at,
                    ats_type=ats,
                    supports_api_apply=api_apply,
                )
            )
        return jobs
