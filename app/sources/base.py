from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.models.user_profile import (
        UserProfile,  # noqa: F401  (kept for backward-compat callers)
    )


class JobData(BaseModel):
    external_id: str
    title: str
    company_name: str
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_raw: str | None = None  # untouched source payload (HTML for greenhouse/lever/ashby)
    salary: str | None = None
    contract_type: str | None = None
    apply_url: str
    posted_at: datetime | None = None


class FetchError(Exception):
    """Base class for adapter fetch failures."""

    def __init__(self, slug: str, message: str = ""):
        self.slug = slug
        super().__init__(message or slug)


class InvalidSlugError(FetchError):
    """404 — board doesn't exist."""


class TransientFetchError(FetchError):
    """5xx, network error, malformed response — retry next cycle."""


class JobSource(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Bare provider key — `'greenhouse'`, `'lever'`, `'ashby'`. Used as
        the value of `Job.source` and `SlugFetch.source`, and as the key in
        `Company.provider_slugs` and the `SOURCES` registry."""

    async def validate(self, slug: str, *, client: httpx.AsyncClient | None = None) -> bool:
        """Cheap existence check via the source's `GET /board/{slug}` style
        endpoint. Returns True iff a posting page exists for `slug`. Default
        implementation raises NotImplementedError; subclasses must override."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_jobs(
        self,
        slug: str,
        *,
        since: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[JobData]:
        """Fetch all currently-listed jobs for `slug`. If `since` is provided,
        filter to postings with `posted_at >= since` (client-side; none of the
        public board endpoints support a server-side date filter)."""
