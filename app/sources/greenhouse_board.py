"""Greenhouse board job source adapter.

Fetches all open jobs from Greenhouse company boards directly using the public
boards API. Driven by the user's profile target_company_slugs["greenhouse"] list.
Non-paginating, non-query-driven.
"""

from datetime import datetime
from typing import Any

import httpx
import structlog

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

    async def _fetch_slug(self, slug: str) -> list[JobData]:
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

        return [j for item in data.get("jobs", []) if (j := self._parse_job(item, slug))]

    async def search(
        self,
        query: str,
        location: str | None,
        slug: str | None = None,
        **kwargs: Any,
    ) -> tuple[list[JobData], None]:
        if slug is None:
            return [], None

        jobs = await self._fetch_slug(slug)
        return jobs, None
