from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobData(BaseModel):
    external_id: str
    title: str
    company_name: str
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_md: str | None = None
    salary: str | None = None
    contract_type: str | None = None
    apply_url: str
    posted_at: datetime | None = None
    ats_type: str | None = None  # greenhouse, lever, ashby
    supports_api_apply: bool = False


class JobSource(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique key used in jobs.source and source_cursors JSONB."""

    @property
    def needs_enrichment(self) -> bool:
        """Whether jobs from this source need a separate enrichment fetch for full descriptions."""
        return True

    @property
    def supports_query_cursor(self) -> bool:
        """Whether this source supports cursor-based pagination per query."""
        return True

    @abstractmethod
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
        """
        Search for jobs matching query/location.
        Returns (list of JobData, next_cursor).
        next_cursor is opaque — stored in source_cursors[source_name].
        """
