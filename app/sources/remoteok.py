"""RemoteOK job source adapter.

Remote-only job board. Free public API, no key required.
Non-paginating: fetches all jobs and filters client-side.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.search_cache import JobSearchCache
from app.sources.ats_detection import detect_ats_type, supports_api_apply
from app.sources.base import JobData, JobSource

REMOTEOK_BASE_URL = "https://remoteok.com/api"

log = structlog.get_logger()


class RemoteOKSource(JobSource):
    @property
    def source_name(self) -> str:
        return "remoteok"

    @property
    def needs_enrichment(self) -> bool:
        return False

    @property
    def supports_query_cursor(self) -> bool:
        return False

    def _make_cache_key(self, query: str, location: str | None) -> str:
        raw = f"remoteok|{query}|{location or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get_cached(self, cache_key: str, session: AsyncSession) -> list | None:
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
        results: list,
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
        cache_key = self._make_cache_key(query, location)

        if session is not None:
            cached = await self._get_cached(cache_key, session)
            if cached is not None:
                return self._parse_results(cached, query), None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    REMOTEOK_BASE_URL,
                    headers={"User-Agent": settings.remoteok_user_agent},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            await log.awarning("remoteok.fetch_failed", error=str(exc))
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

        return self._parse_results(data, query), None

    def _parse_results(self, data: list, query: str) -> list[JobData]:
        # data[0] is metadata — skip it
        items = data[1:] if len(data) > 1 else []

        query_tokens = query.lower().split() if query.strip() else []

        jobs = []
        for item in items:
            if not isinstance(item, dict):
                continue

            apply_url = item.get("url", "")
            if not apply_url:
                continue

            # Client-side filtering by query tokens
            if query_tokens:
                tags = item.get("tags") or []
                searchable = (item.get("position", "") + " " + " ".join(tags)).lower()
                if not any(token in searchable for token in query_tokens):
                    continue

            ats = detect_ats_type(apply_url)
            api_apply = supports_api_apply(apply_url)

            location = item.get("location") or None
            if location == "":
                location = None

            posted_at = None
            if epoch := item.get("epoch"):
                try:
                    posted_at = datetime.fromtimestamp(int(epoch), tz=UTC)
                except (ValueError, OSError):
                    pass

            salary = None
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            if salary_min and salary_max:
                salary = f"${salary_min:,} – ${salary_max:,}"

            jobs.append(
                JobData(
                    external_id=str(item.get("id", "")),
                    title=item.get("position", ""),
                    company_name=item.get("company") or "",
                    location=location,
                    workplace_type="remote",
                    description_md=item.get("description"),
                    salary=salary,
                    contract_type=None,
                    apply_url=apply_url,
                    posted_at=posted_at,
                    ats_type=ats,
                    supports_api_apply=api_apply,
                )
            )
        return jobs
