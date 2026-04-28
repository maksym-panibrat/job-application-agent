"""Greenhouse board job source adapter.

Fetches all open jobs from Greenhouse company boards directly using the public
boards API. Driven by the user's profile target_company_slugs["greenhouse"] list.
Non-paginating, non-query-driven.
"""

from datetime import datetime
from typing import Any

import httpx
import markdownify
import structlog

from app.sources.base import JobData, JobSource

GREENHOUSE_BOARDS_BASE = "https://boards-api.greenhouse.io/v1/boards"

log = structlog.get_logger()


def _html_to_markdown(content: str | None) -> str | None:
    """Convert Greenhouse HTML `content` to Markdown so the field name matches
    reality and the matching LLM doesn't burn tokens on tags (issue #51)."""
    if not content:
        return content
    return markdownify.markdownify(content, strip=["script", "style"]).strip() or None


class GreenhouseFetchError(Exception):
    """Base for slug-fetch failures the caller should surface."""

    def __init__(self, slug: str, message: str = ""):
        self.slug = slug
        super().__init__(message or slug)


class InvalidSlugError(GreenhouseFetchError):
    """Greenhouse returned 404 — the slug does not exist on their platform."""


class TransientFetchError(GreenhouseFetchError):
    """5xx or network error — retry on the next sync."""


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
            description_md=_html_to_markdown(item.get("content")),
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
                "greenhouse_board.upstream_5xx",
                slug=slug,
                status=response.status_code,
            )
            raise TransientFetchError(slug, f"upstream {response.status_code}")
        try:
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
            raise TransientFetchError(slug, str(exc)) from exc

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
