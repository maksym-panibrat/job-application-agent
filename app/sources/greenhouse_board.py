"""Greenhouse board job source adapter."""

from datetime import datetime

import httpx
import structlog

from app.data.slug_company import slug_to_company_name
from app.sources.base import (
    InvalidSlugError,
    JobData,
    JobSource,
    TransientFetchError,
)

GREENHOUSE_BOARDS_BASE = "https://boards-api.greenhouse.io/v1/boards"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

log = structlog.get_logger()


class GreenhouseBoardSource(JobSource):
    @property
    def provider_name(self) -> str:
        return "greenhouse"

    def _parse_job(self, item: dict, slug: str) -> JobData | None:
        job_id = item.get("id")
        title = item.get("title", "")
        apply_url = item.get("absolute_url", "")
        if not apply_url:
            return None
        company_name = slug_to_company_name(slug)
        location_obj = item.get("location") or {}
        location = location_obj.get("name") or None
        workplace_type = "remote" if (location and "remote" in location.lower()) else None
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
            # raw HTML; clean_html_to_markdown runs in job_service
            description_raw=item.get("content"),
            salary=None,
            contract_type=None,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    async def validate(self, slug: str, *, client: httpx.AsyncClient | None = None) -> bool:
        url = f"{GREENHOUSE_BOARDS_BASE}/{slug}"
        try:
            if client is not None:
                resp = await client.get(url)
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    resp = await c.get(url)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def _fetch_slug(
        self,
        slug: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[JobData]:
        url = f"{GREENHOUSE_BOARDS_BASE}/{slug}/jobs"
        params = {"content": "true"}
        try:
            if client is not None:
                response = await client.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    response = await c.get(url, params=params)
        except httpx.HTTPError as exc:
            await log.awarning(
                "greenhouse_board.network_error",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise TransientFetchError(slug, str(exc)) from exc

        if response.status_code == 404:
            await log.awarning("greenhouse_board.invalid_slug", slug=slug)
            raise InvalidSlugError(slug, "board not found")
        if response.status_code >= 500:
            await log.awarning(
                "greenhouse_board.upstream_5xx", slug=slug, status=response.status_code
            )
            raise TransientFetchError(slug, f"upstream {response.status_code}")
        try:
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            await log.aerror(
                "greenhouse_board.fetch_failed",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise TransientFetchError(slug, str(exc)) from exc

        return [j for item in data.get("jobs", []) if (j := self._parse_job(item, slug))]

    async def fetch_jobs(
        self,
        slug: str,
        *,
        since: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[JobData]:
        jobs = await self._fetch_slug(slug, client=client)
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is None or j.posted_at >= since]
