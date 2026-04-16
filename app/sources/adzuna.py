"""Adzuna job search source adapter."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.search_cache import JobSearchCache
from app.sources.ats_detection import detect_ats_type, supports_api_apply
from app.sources.base import JobData, JobSource

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs"


class AdzunaSource(JobSource):
    @property
    def source_name(self) -> str:
        return "adzuna"

    def _make_cache_key(self, query: str, location: str | None, page: int) -> str:
        raw = f"adzuna|{query}|{location or ''}|{page}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _get_cached(
        self, cache_key: str, session: AsyncSession
    ) -> dict | None:
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
        # Upsert: delete old entry if exists, then insert
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
    ) -> tuple[list[JobData], Any]:
        """
        Search Adzuna for jobs. cursor = page number (int, default 1).
        Returns (list[JobData], next_page_number).
        """
        page = cursor if isinstance(cursor, int) and cursor > 0 else 1

        if not settings.adzuna_app_id or not settings.adzuna_api_key.get_secret_value():
            return [], page

        cache_key = self._make_cache_key(query, location, page)
        cached = await self._get_cached(cache_key, session)
        if cached:
            return self._parse_results(cached), page + 1

        country = "us"
        url = f"{ADZUNA_BASE_URL}/{country}/search/{page}"
        params: dict[str, Any] = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_api_key.get_secret_value(),
            "what": query,
            "results_per_page": 20,
            "content-type": "application/json",
        }
        if location:
            params["where"] = location
            params["distance"] = settings.adzuna_search_distance_km

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        await self._save_cache(
            cache_key, query, location, data, settings.adzuna_cache_ttl_hours, session
        )
        return self._parse_results(data), page + 1

    def _parse_results(self, data: dict) -> list[JobData]:
        jobs = []
        for item in data.get("results", []):
            apply_url = item.get("redirect_url", "")
            ats = detect_ats_type(apply_url)
            api_apply = supports_api_apply(apply_url)

            posted_at = None
            if created := item.get("created"):
                try:
                    posted_at = datetime.fromisoformat(created.rstrip("Z")).replace(tzinfo=UTC)
                except ValueError:
                    pass

            jobs.append(
                JobData(
                    external_id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    company_name=item.get("company", {}).get("display_name", ""),
                    location=item.get("location", {}).get("display_name"),
                    workplace_type=None,  # Adzuna doesn't expose this reliably
                    description_md=item.get("description", ""),
                    apply_url=apply_url,
                    posted_at=posted_at,
                    ats_type=ats,
                    supports_api_apply=api_apply,
                )
            )
        return jobs
