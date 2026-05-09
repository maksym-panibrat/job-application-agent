"""Ashby board job source adapter.

Public posting endpoint, no auth:
  GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Returns the entire board in one shot — no pagination. Ashby's public response
doesn't expose a stable numeric id; we use jobUrl (with tracking query params
stripped) as external_id since it's canonical and idempotent across fetches.
"""

from datetime import datetime
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit

import httpx
import structlog

from app.sources.base import (
    InvalidSlugError,
    JobData,
    JobSource,
    TransientFetchError,
)

ASHBY_POSTINGS_BASE = "https://api.ashbyhq.com/posting-api/job-board"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

log = structlog.get_logger()


def _strip_tracking(url: str) -> str:
    """Drop the query string and fragment so the same posting always hashes
    to the same external_id."""
    if not url:
        return url
    no_frag, _ = urldefrag(url)
    parts = urlsplit(no_frag)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class AshbyBoardSource(JobSource):
    @property
    def provider_name(self) -> str:
        return "ashby"

    def _parse_posting(self, item: dict, slug: str) -> JobData | None:
        if not item.get("isListed", True):
            return None
        apply_url = item.get("applyUrl") or ""
        if not apply_url:
            return None
        job_url = item.get("jobUrl") or ""
        external_id = _strip_tracking(job_url) or apply_url
        if not external_id:
            return None
        title = item.get("title", "")
        location = item.get("location") or None
        workplace_type = (item.get("workplaceType") or "").lower() or None
        contract_type = item.get("employmentType") or None
        posted_at = None
        if ts := item.get("publishedAt"):
            try:
                posted_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        # Ashby's public response doesn't expose a canonical company name
        # field; the slug itself is canonical for the Company row, and
        # Track B will replace this lazy derivation with Company.canonical_name.
        company_name = slug.replace("-", " ").title()
        return JobData(
            external_id=external_id,
            title=title,
            company_name=company_name,
            location=location,
            workplace_type=workplace_type,
            description_raw=item.get("descriptionHtml") or None,
            salary=None,
            contract_type=contract_type,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    async def _request(self, slug: str, *, client: httpx.AsyncClient | None) -> Any:
        url = f"{ASHBY_POSTINGS_BASE}/{slug}"
        params = {"includeCompensation": "true"}
        try:
            if client is not None:
                response = await client.get(url, params=params)
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    response = await c.get(url, params=params)
        except httpx.HTTPError as exc:
            await log.awarning(
                "ashby_board.network_error",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise TransientFetchError(slug, str(exc)) from exc

        if response.status_code == 404:
            await log.awarning("ashby_board.invalid_slug", slug=slug)
            raise InvalidSlugError(slug, "board not found")
        if response.status_code >= 500:
            await log.awarning("ashby_board.upstream_5xx", slug=slug, status=response.status_code)
            raise TransientFetchError(slug, f"upstream {response.status_code}")
        try:
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            await log.aerror(
                "ashby_board.fetch_failed",
                slug=slug,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise TransientFetchError(slug, str(exc)) from exc

    async def validate(self, slug: str, *, client: httpx.AsyncClient | None = None) -> bool:
        url = f"{ASHBY_POSTINGS_BASE}/{slug}"
        try:
            if client is not None:
                resp = await client.get(url, params={"includeCompensation": "false"})
            else:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
                    resp = await c.get(url, params={"includeCompensation": "false"})
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
        data = await self._request(slug, client=client)
        items = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        jobs = [j for item in items if (j := self._parse_posting(item, slug))]
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is None or j.posted_at >= since]
