"""JSearch (RapidAPI) job source adapter.

Returns direct employer/ATS URLs and full descriptions.
Free tier: 500 requests/month. Set JSEARCH_API_KEY in .env to enable.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.search_cache import JobSearchCache
from app.sources.ats_detection import detect_ats_type, supports_api_apply
from app.sources.base import JobData, JobSource

JSEARCH_BASE_URL = "https://jsearch.p.rapidapi.com/search"


class JSearchSource(JobSource):
    @property
    def source_name(self) -> str:
        return "jsearch"

    @property
    def needs_enrichment(self) -> bool:
        return False  # JSearch returns full descriptions and direct URLs

    def _make_cache_key(self, query: str, location: str | None, page: int) -> str:
        raw = f"jsearch|{query}|{location or ''}|{page}"
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
    ) -> tuple[list[JobData], Any]:
        """
        Search JSearch for jobs. cursor = page number (int, default 1).
        Returns (list[JobData], next_page_number).
        """
        page = cursor if isinstance(cursor, int) and cursor > 0 else 1
        api_key = settings.jsearch_api_key.get_secret_value()
        if not api_key:
            return [], page

        # Build search query — JSearch accepts "query in location" format
        search_query = f"{query} in {location}" if location else query

        cache_key = self._make_cache_key(query, location, page)
        cached = await self._get_cached(cache_key, session)
        if cached:
            return self._parse_results(cached, settings), page + 1

        params: dict[str, Any] = {
            "query": search_query,
            "page": page,
            "num_pages": 1,
            "date_posted": "month",
        }
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(JSEARCH_BASE_URL, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        await self._save_cache(
            cache_key, query, location, data, settings.adzuna_cache_ttl_hours, session
        )
        return self._parse_results(data, settings), page + 1

    def _parse_results(self, data: dict, settings: Any) -> list[JobData]:
        jobs = []
        max_results = getattr(settings, "jsearch_max_results_per_query", 10)

        for item in data.get("data", [])[:max_results]:
            apply_url = item.get("job_apply_link", "")
            if not apply_url:
                continue

            ats = detect_ats_type(apply_url)
            api_apply = supports_api_apply(apply_url)

            # Location: prefer city+state
            city = item.get("job_city") or ""
            state = item.get("job_state") or ""
            location = ", ".join(filter(None, [city, state])) or item.get("job_country") or None

            workplace_type = None
            if item.get("job_is_remote"):
                workplace_type = "remote"

            # Salary: format range if available
            salary = None
            min_sal = item.get("job_min_salary")
            max_sal = item.get("job_max_salary")
            period = item.get("job_salary_period", "")
            if min_sal and max_sal:
                salary = f"${int(min_sal):,} – ${int(max_sal):,}"
                if period:
                    salary += f" {period}"
            elif min_sal or max_sal:
                salary = f"${int(min_sal or max_sal):,}"
                if period:
                    salary += f" {period}"

            posted_at = None
            if ts := item.get("job_posted_at_datetime_utc"):
                try:
                    posted_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass

            jobs.append(
                JobData(
                    external_id=item.get("job_id", ""),
                    title=item.get("job_title", ""),
                    company_name=item.get("employer_name", ""),
                    location=location,
                    workplace_type=workplace_type,
                    description_md=item.get("job_description", ""),
                    salary=salary,
                    contract_type=item.get("job_employment_type"),
                    apply_url=apply_url,
                    posted_at=posted_at,
                    ats_type=ats,
                    supports_api_apply=api_apply,
                )
            )
        return jobs
