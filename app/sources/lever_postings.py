"""Lever postings job source adapter.

Public board endpoint, no auth:
  GET https://api.lever.co/v0/postings/{slug}?mode=json&skip=X&limit=Y

Lever paginates; we loop skip+=100 until an empty page returns. We always
read `descriptionHtml` for description_raw so the html_cleaner pipeline
produces uniform markdown across providers.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.sources.base import (
    InvalidSlugError,
    JobData,
    JobSource,
    TransientFetchError,
)
from app.sources.salary import extract_salary_range_from_text, format_salary_range

LEVER_POSTINGS_BASE = "https://api.lever.co/v0/postings"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
PAGE_LIMIT = 100

log = structlog.get_logger()


class LeverPostingsSource(JobSource):
    @property
    def provider_name(self) -> str:
        return "lever"

    def _parse_posting(self, item: dict, slug: str) -> JobData | None:
        apply_url = item.get("applyUrl") or ""
        if not apply_url:
            return None
        external_id = str(item.get("id") or "")
        if not external_id:
            return None
        title = item.get("text", "")
        categories = item.get("categories") or {}
        location = categories.get("location") or None
        workplace_type = item.get("workplaceType") or None
        contract_type = categories.get("commitment") or None
        salary_obj = item.get("salaryRange") or {}
        salary = format_salary_range(
            salary_obj.get("min"),
            salary_obj.get("max"),
            salary_obj.get("currency"),
        )
        if salary is None:
            salary = extract_salary_range_from_text(
                item.get("salaryDescriptionPlain")
                or item.get("salaryDescription")
                or item.get("descriptionHtml")
                or item.get("description")
            )
        posted_at = None
        if ts := item.get("createdAt"):
            try:
                posted_at = datetime.fromtimestamp(ts / 1000, tz=UTC)
            except (TypeError, ValueError, OSError):
                pass
        # Lever's `categories.team` is the closest analogue to a company name in
        # this slug-only flow; the slug itself is canonical for the Company row.
        company_name = slug.replace("-", " ").title()
        return JobData(
            external_id=external_id,
            title=title,
            company_name=company_name,
            location=location,
            workplace_type=workplace_type,
            description_raw=item.get("descriptionHtml") or item.get("description") or None,
            salary=salary,
            contract_type=contract_type,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    async def _request(self, slug: str, params: dict, *, client: httpx.AsyncClient | None) -> Any:
        url = f"{LEVER_POSTINGS_BASE}/{slug}"
        try:
            if client is not None:
                response = await client.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    response = await c.get(url, params=params)
        except httpx.HTTPError as exc:
            await log.awarning(
                "lever_postings.network_error",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise TransientFetchError(slug, str(exc)) from exc

        if response.status_code == 404:
            await log.awarning("lever_postings.invalid_slug", slug=slug)
            raise InvalidSlugError(slug, "site not found")
        if response.status_code >= 500:
            await log.awarning(
                "lever_postings.upstream_5xx", slug=slug, status=response.status_code
            )
            raise TransientFetchError(slug, f"upstream {response.status_code}")
        try:
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            await log.aerror(
                "lever_postings.fetch_failed",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise TransientFetchError(slug, str(exc)) from exc

    async def validate(self, slug: str, *, client: httpx.AsyncClient | None = None) -> bool:
        url = f"{LEVER_POSTINGS_BASE}/{slug}"
        try:
            if client is not None:
                resp = await client.get(url, params={"mode": "json", "limit": 1})
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    resp = await c.get(url, params={"mode": "json", "limit": 1})
        except httpx.HTTPError as exc:
            # Network blip looks identical to a confirmed 404 if we collapse to
            # False here. Raise so company_resolver._fan_out maps this to
            # "error" — a later sync cycle's SlugFetch retry can repair the gap.
            raise TransientFetchError(slug, str(exc)) from exc
        if resp.status_code == 404:
            return False  # confirmed miss
        if resp.status_code >= 500:
            raise TransientFetchError(slug, f"upstream {resp.status_code}")
        return resp.status_code == 200

    async def fetch_jobs(
        self,
        slug: str,
        *,
        since: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[JobData]:
        jobs: list[JobData] = []
        skip = 0
        while True:
            params = {"mode": "json", "skip": skip, "limit": PAGE_LIMIT}
            data = await self._request(slug, params, client=client)
            if not isinstance(data, list) or not data:
                break
            for item in data:
                if (jd := self._parse_posting(item, slug)) is not None:
                    jobs.append(jd)
            if len(data) < PAGE_LIMIT:
                break
            skip += PAGE_LIMIT
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is None or j.posted_at >= since]
