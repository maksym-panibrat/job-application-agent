# Provider-agnostic companies (Lever + Ashby) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land a `Company` entity, Lever + Ashby `JobSource` adapters, JIT fan-out resolver across all three providers, and the description-field rename in one PR. Frontend stops exposing ATS providers; users follow companies by name.

**Architecture:** Backend introduces `app.sources.SOURCES` (provider → adapter dict) so the scheduler dispatches by name. A new `companies` table holds `(canonical_name, normalized_key, provider_slugs JSONB)`. The resolver service `app.services.company_resolver.resolve()` runs cache-lookup → parallel `validate()` fan-out → persist-with-`ON CONFLICT`. `user_profiles.target_company_ids UUID[]` replaces the old per-provider JSON dict. `Job.description_md` is renamed to `description_raw` (untouched source HTML), `Job.description_clean` to `description` (canonical markdown); the Greenhouse adapter loses its double-clean bug. Frontend's `TargetSlugsSection.tsx` becomes `FollowedCompaniesSection.tsx` — a single text input that POSTs `/api/companies/resolve`.

**Tech Stack:** FastAPI, SQLModel, Alembic, Postgres + JSONB, httpx, structlog, asyncio. Frontend: React + TypeScript + Vite + TanStack Query + Tailwind. Tests: pytest + testcontainers + Vitest.

**Spec:** `docs/superpowers/specs/2026-05-08-provider-agnostic-companies-design.md`.

---

## File map

### Created
- `app/sources/lever_postings.py` — Lever `JobSource` adapter (paginated `?skip=X&limit=Y`).
- `app/sources/ashby_board.py` — Ashby `JobSource` adapter (no pagination).
- `app/services/company_resolver.py` — `resolve()` service with fan-out and cache.
- `app/api/companies.py` — `POST /api/companies/resolve` endpoint.
- `app/models/company.py` — `Company` SQLModel.
- `alembic/versions/<id>_add_companies_and_rename_description.py` — single migration revision: schema + data backfill + provider name normalization + description renames.
- `tests/unit/sources/test_lever_postings.py`
- `tests/unit/sources/test_ashby_board.py`
- `tests/unit/services/test_company_resolver.py`
- `tests/unit/api/test_companies_resolve.py`
- `tests/integration/test_company_resolution_flow.py`
- `tests/integration/test_migration_companies.py`
- `tests/smoke/test_company_resolution.py`
- `frontend/src/components/settings/FollowedCompaniesSection.tsx` (replaces `TargetSlugsSection.tsx`).
- `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`.

### Modified
- `app/sources/base.py` — lift `validate`, `fetch_jobs`, `InvalidSlugError`, `TransientFetchError` to base; rename `source_name` → `provider_name`; drop unused `search()` from the abstract contract.
- `app/sources/__init__.py` — export `SOURCES: dict[str, JobSource]`.
- `app/sources/greenhouse_board.py` — drop `_html_to_markdown` (defeats `description_raw` purpose), set `description_raw=item.get("content")` directly, `provider_name` returns `"greenhouse"`.
- `app/models/job.py` — rename `description_md` → `description_raw`, `description_clean` → `description`; add `company_id: UUID | None` FK.
- `app/models/user_profile.py` — add `target_company_ids: list[UUID]`; mark `target_company_slugs` deprecated (kept for rollback).
- `app/models/__init__.py` — register `Company`.
- `app/sources/base.py::JobData` — rename `description_md` → `description_raw`.
- `app/services/job_service.py` — read/write `description_raw`/`description`; produce `description` via existing `clean_html_to_markdown`.
- `app/services/slug_registry_service.py` — generalize `validate_slug` to dispatch via `SOURCES`; rewrite `enqueue_stale` to walk `Company.provider_slugs`; relocate invalid-slug pruning to operate on `Company.provider_slugs` instead of profile JSON.
- `app/services/job_sync_service.py` — `_prune_invalid_slugs` rewrite (now `_prune_invalid_provider_slugs`); read profile via `target_company_ids`; same return shape so callers don't change.
- `app/scheduler/tasks.py` — `run_sync_queue` dispatches via `SOURCES[row.source]` instead of instantiating `GreenhouseBoardSource`.
- `app/api/profile.py` — `GET /api/profile` surfaces `target_companies: [{id, canonical_name}]` (resolved server-side); `PATCH /api/profile` accepts `target_company_ids` instead of `target_company_slugs`.
- `app/agents/onboarding.py` — system prompt rewrite (lines ~40–80), tool schema (`target_companies: list[str]`), `persist_inferred_slugs` → `persist_inferred_companies`, completion gate, status renderer.
- `app/main.py` — register `companies` router.
- `frontend/src/api/client.ts` — drop `target_company_slugs` from `Profile` type; add `target_companies: { id, canonical_name }[]`; add `resolveCompany(name)`; `updateProfile` accepts `target_company_ids`.
- `frontend/src/pages/Settings.tsx` (or wherever `TargetSlugsSection` is rendered) — render `FollowedCompaniesSection` with the new `target_companies` shape.
- `frontend/src/components/settings/__tests__/TargetSlugsSection.test.tsx` (if present) — delete or rewrite as `FollowedCompaniesSection.test.tsx`.
- Test fixtures in `tests/conftest.py` and any `tests/integration/*` that hardcode `source="greenhouse_board"` → `"greenhouse"`.

### Deleted
- `frontend/src/components/settings/TargetSlugsSection.tsx` — replaced by `FollowedCompaniesSection.tsx`.

---

# Track A — Adapter foundation

Goal: lift shared adapter machinery to the base class, add Lever and Ashby adapters, expose a `SOURCES` registry. No DB changes. Keep `provider_name` returning `"greenhouse_board"` until Track B's migration UPDATEs the column values — switching the constant before the migration would break the existing test suite.

### Task A1: Lift base methods, drop `search()`, lift exceptions

**Files:**
- Modify: `app/sources/base.py`
- Modify: `app/sources/greenhouse_board.py`
- Test: `tests/unit/sources/test_base.py` (new)

Existing `JobSource.search()` is unused by the slug-flow (only the slug→jobs path is wired). `validate()` and `fetch_jobs()` exist only on `GreenhouseBoardSource`. `InvalidSlugError` and `TransientFetchError` live in `greenhouse_board.py` even though they're a generic adapter contract. Lift everything to `base.py` so Lever and Ashby inherit the contract, not just the structure.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/sources/test_base.py`:

```python
"""Contract tests for JobSource base class."""

import inspect

import pytest

from app.sources.base import InvalidSlugError, JobSource, TransientFetchError


def test_jobsource_is_abstract_for_provider_name_and_fetch_jobs():
    """The base class must require provider_name and fetch_jobs; validate has a
    sensible default that subclasses can override but don't have to."""
    with pytest.raises(TypeError):
        JobSource()  # abstract — provider_name + fetch_jobs unimplemented


def test_invalid_slug_error_lives_in_base():
    """Both error types must be importable from app.sources.base — adapters
    raise these from their fetch path and the scheduler branches on them."""
    assert issubclass(InvalidSlugError, Exception)
    assert issubclass(TransientFetchError, Exception)


def test_jobsource_no_search_method():
    """search() was unused; removing it means no half-implemented flag method
    is left on the abstract class."""
    assert not hasattr(JobSource, "search"), (
        "search() should be removed from the JobSource contract"
    )


def test_jobsource_has_validate_and_fetch_jobs():
    """validate() and fetch_jobs() are now part of the base contract."""
    assert "validate" in JobSource.__abstractmethods__ or callable(getattr(JobSource, "validate", None))
    assert "fetch_jobs" in JobSource.__abstractmethods__
    assert "provider_name" in JobSource.__abstractmethods__


def test_provider_name_returns_string():
    """Concrete adapters must implement provider_name as a property returning str."""
    from app.sources.greenhouse_board import GreenhouseBoardSource

    src = GreenhouseBoardSource()
    assert isinstance(src.provider_name, str)
    assert src.provider_name == "greenhouse_board"  # rename to "greenhouse" happens in Track B
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/sources/test_base.py -v`
Expected: FAIL — `JobSource.search` still exists, `provider_name` doesn't exist (currently `source_name`), exceptions are in `greenhouse_board`.

- [ ] **Step 3: Rewrite `app/sources/base.py`**

Replace the file with:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.models.user_profile import UserProfile  # noqa: F401  (kept for backward-compat callers)


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
```

Notes:
- `JobData.description_md` is renamed to `description_raw` here. `Job` model and `job_service` updates land in Track B but `JobData` is the wire contract between adapters and the service, so it changes alongside the lift.
- `description_html` is *not* a thing: we always store HTML in `description_raw` and let the cleaner produce markdown into `Job.description`.

- [ ] **Step 4: Update `app/sources/greenhouse_board.py`**

Replace the file (verbatim — kept short for the engineer to paste):

```python
"""Greenhouse board job source adapter."""

from datetime import datetime
from typing import Any

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
        # Track B's migration flips this to "greenhouse" alongside the
        # UPDATE jobs SET source = 'greenhouse' WHERE source = 'greenhouse_board'.
        return "greenhouse_board"

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
            description_raw=item.get("content"),  # raw HTML; clean_html_to_markdown runs in job_service
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
```

Key changes vs. current code:
- `_html_to_markdown` deleted; `description_raw=item.get("content")` is the raw HTML passthrough. The double-clean bug dies.
- `source_name` → `provider_name` (still returns `"greenhouse_board"` until Track B).
- `search()` removed.
- `InvalidSlugError`/`TransientFetchError` imported from `base`.

- [ ] **Step 5: Update existing greenhouse adapter tests for the field rename**

Run: `rg -n "description_md" tests/unit/sources/ tests/integration/`
Expected: any usage in greenhouse-related tests; rename to `description_raw` mechanically.

If `tests/unit/sources/test_greenhouse_board.py` exists, replace `description_md` with `description_raw` and update any `_html_to_markdown` assertions to expect raw HTML in `description_raw`.

- [ ] **Step 6: Run all unit tests**

Run: `uv run pytest tests/unit/sources/ -v`
Expected: PASS — base contract tests pass, greenhouse tests pass with new field name.

- [ ] **Step 7: Commit**

```bash
git add app/sources/base.py app/sources/greenhouse_board.py tests/unit/sources/test_base.py tests/unit/sources/test_greenhouse_board.py
git commit -m "$(cat <<'EOF'
refactor(sources): lift JobSource contract to base, drop greenhouse double-clean

- Lift validate(), fetch_jobs(), InvalidSlugError, TransientFetchError to
  app/sources/base.py so adapters share the contract, not just the structure.
- Rename JobSource.source_name -> provider_name (return value flips to bare
  "greenhouse" in Track B alongside the UPDATE on jobs.source).
- Drop unused JobSource.search() from the abstract contract.
- JobData.description_md -> description_raw (untouched source payload).
- Greenhouse adapter stops markdownifying HTML before handing to job_service;
  the cleaner pipeline in job_service produces description_clean -> description
  consistently for every source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Lever postings adapter

**Files:**
- Create: `app/sources/lever_postings.py`
- Test: `tests/unit/sources/test_lever_postings.py`

Endpoint: `GET https://api.lever.co/v0/postings/{slug}?mode=json&skip=X&limit=Y`. No auth. Lever paginates; we loop `skip += 100` until an empty page returns. Per the spec, we always take `descriptionHtml` (HTML) for `description_raw` so the cleaner pipeline produces consistent markdown.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/sources/test_lever_postings.py`:

```python
"""Tests for the Lever postings adapter."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.sources.base import InvalidSlugError, TransientFetchError
from app.sources.lever_postings import LEVER_POSTINGS_BASE, LeverPostingsSource


@pytest.fixture
def src():
    return LeverPostingsSource()


def _posting(idx: int, posted_iso: str = "2026-05-01T12:00:00Z") -> dict:
    return {
        "id": f"posting-{idx}",
        "text": f"Title {idx}",
        "descriptionHtml": f"<p>Body {idx}</p>",
        "descriptionPlain": f"Body {idx}",
        "categories": {
            "location": "Remote — US",
            "team": "Engineering",
            "commitment": "Full-time",
        },
        "hostedUrl": f"https://jobs.lever.co/acme/posting-{idx}",
        "applyUrl": f"https://jobs.lever.co/acme/posting-{idx}/apply",
        "createdAt": int(datetime.fromisoformat(posted_iso.replace("Z", "+00:00")).timestamp() * 1000),
        "workplaceType": "remote",
    }


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_true_on_200(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(200, json=[])
    async with httpx.AsyncClient() as client:
        assert await src.validate("acme", client=client) is True


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_false_on_404(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        assert await src.validate("missing", client=client) is False


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_single_page(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[_posting(1), _posting(2)]),
            httpx.Response(200, json=[]),  # empty page ends the loop
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["posting-1", "posting-2"]
    assert all(j.description_raw == f"<p>Body {i}</p>" for i, j in enumerate(jobs, start=1))
    assert jobs[0].apply_url == "https://jobs.lever.co/acme/posting-1/apply"
    assert jobs[0].workplace_type == "remote"
    assert jobs[0].location == "Remote — US"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_paginates_until_empty(src):
    page1 = [_posting(i) for i in range(100)]
    page2 = [_posting(i) for i in range(100, 150)]
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert len(jobs) == 150


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_404_raises_invalid_slug(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSlugError):
            await src.fetch_jobs("missing", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_5xx_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(503)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_network_error_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_malformed_json_raises_transient(src):
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").respond(200, content=b"not json")
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_postings_without_apply_url(src):
    bad = _posting(1)
    bad["applyUrl"] = ""
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[bad, _posting(2)]),
            httpx.Response(200, json=[]),
        ]
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["posting-2"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_filters_by_since(src):
    recent = _posting(1, "2026-05-05T12:00:00Z")
    old = _posting(2, "2025-01-01T00:00:00Z")
    respx.get(f"{LEVER_POSTINGS_BASE}/acme").mock(
        side_effect=[
            httpx.Response(200, json=[recent, old]),
            httpx.Response(200, json=[]),
        ]
    )
    cutoff = datetime.now(UTC) - timedelta(days=14)
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", since=cutoff, client=client)
    assert [j.external_id for j in jobs] == ["posting-1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/sources/test_lever_postings.py -v`
Expected: FAIL — `LeverPostingsSource` doesn't exist yet.

- [ ] **Step 3: Implement `app/sources/lever_postings.py`**

```python
"""Lever postings job source adapter.

Public board endpoint, no auth:
  GET https://api.lever.co/v0/postings/{slug}?mode=json&skip=X&limit=Y

Lever paginates; we loop skip+=100 until an empty page returns. We always
read `descriptionHtml` for description_raw so the html_cleaner pipeline
produces uniform markdown across providers.
"""

from datetime import datetime
from typing import Any

import httpx
import structlog

from app.sources.base import (
    InvalidSlugError,
    JobData,
    JobSource,
    TransientFetchError,
)

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
        salary = None
        if salary_obj.get("min") is not None and salary_obj.get("max") is not None:
            currency = salary_obj.get("currency") or ""
            salary = f"{currency}{salary_obj['min']}–{salary_obj['max']}"
        posted_at = None
        if ts := item.get("createdAt"):
            try:
                posted_at = datetime.fromtimestamp(ts / 1000, tz=__import__("datetime").timezone.utc)
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

    async def _request(
        self, slug: str, params: dict, *, client: httpx.AsyncClient | None
    ) -> Any:
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
        except httpx.HTTPError:
            return False
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/sources/test_lever_postings.py -v`
Expected: PASS.

- [ ] **Step 5: Verify `respx` is installed**

Run: `uv run python -c "import respx; print(respx.__version__)"`
Expected: a version printed. If it errors, run `uv add --dev respx` and re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add app/sources/lever_postings.py tests/unit/sources/test_lever_postings.py
git commit -m "$(cat <<'EOF'
feat(sources): add Lever postings adapter

Public board endpoint (api.lever.co/v0/postings/{slug}), no auth. Loops
skip+=100 until empty page returns. description_raw stores raw HTML so the
shared html_cleaner pipeline produces uniform markdown.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A3: Ashby board adapter

**Files:**
- Create: `app/sources/ashby_board.py`
- Test: `tests/unit/sources/test_ashby_board.py`

Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`. No auth. Single-shot — Ashby returns the entire board. `external_id` = `posting.jobUrl` (Ashby doesn't expose a stable id; jobUrl is canonical), with any tracking query params stripped.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/sources/test_ashby_board.py`:

```python
"""Tests for the Ashby board adapter."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.sources.ashby_board import ASHBY_POSTINGS_BASE, AshbyBoardSource
from app.sources.base import InvalidSlugError, TransientFetchError


@pytest.fixture
def src():
    return AshbyBoardSource()


def _posting(idx: int, posted_iso: str = "2026-05-01T12:00:00Z") -> dict:
    return {
        "title": f"Title {idx}",
        "department": "Engineering",
        "team": "Platform",
        "descriptionHtml": f"<p>Body {idx}</p>",
        "descriptionPlain": f"Body {idx}",
        "publishedAt": posted_iso,
        "employmentType": "FullTime",
        "jobUrl": f"https://jobs.ashbyhq.com/acme/job-{idx}?utm_source=board",
        "applyUrl": f"https://jobs.ashbyhq.com/acme/job-{idx}/application",
        "isListed": True,
        "workplaceType": "Remote",
        "location": "San Francisco, CA",
        "secondaryLocations": [],
        "address": {},
    }


def _payload(*postings: dict) -> dict:
    return {"jobs": list(postings)}


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_true_on_200(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload())
    async with httpx.AsyncClient() as client:
        assert await src.validate("acme", client=client) is True


@respx.mock
@pytest.mark.asyncio
async def test_validate_returns_false_on_404(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        assert await src.validate("missing", client=client) is False


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_happy_path(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(
        200, json=_payload(_posting(1), _posting(2))
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert len(jobs) == 2
    assert jobs[0].description_raw == "<p>Body 1</p>"
    # external_id is the jobUrl with tracking params stripped.
    assert jobs[0].external_id == "https://jobs.ashbyhq.com/acme/job-1"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_postings_without_apply_url(src):
    bad = _posting(1)
    bad["applyUrl"] = ""
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(
        200, json=_payload(bad, _posting(2))
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.external_id for j in jobs] == ["https://jobs.ashbyhq.com/acme/job-2"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_404_raises_invalid_slug(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/missing").respond(404)
    async with httpx.AsyncClient() as client:
        with pytest.raises(InvalidSlugError):
            await src.fetch_jobs("missing", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_5xx_raises_transient(src):
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(502)
    async with httpx.AsyncClient() as client:
        with pytest.raises(TransientFetchError):
            await src.fetch_jobs("acme", client=client)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_filters_by_since(src):
    recent = _posting(1, "2026-05-05T12:00:00Z")
    old = _posting(2, "2025-01-01T00:00:00Z")
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(200, json=_payload(recent, old))
    cutoff = datetime.now(UTC) - timedelta(days=14)
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", since=cutoff, client=client)
    assert [j.title for j in jobs] == ["Title 1"]


@respx.mock
@pytest.mark.asyncio
async def test_fetch_jobs_skips_unlisted(src):
    unlisted = _posting(1)
    unlisted["isListed"] = False
    respx.get(f"{ASHBY_POSTINGS_BASE}/acme").respond(
        200, json=_payload(unlisted, _posting(2))
    )
    async with httpx.AsyncClient() as client:
        jobs = await src.fetch_jobs("acme", client=client)
    assert [j.title for j in jobs] == ["Title 2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/sources/test_ashby_board.py -v`
Expected: FAIL — `AshbyBoardSource` doesn't exist yet.

- [ ] **Step 3: Implement `app/sources/ashby_board.py`**

```python
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
            await log.awarning(
                "ashby_board.upstream_5xx", slug=slug, status=response.status_code
            )
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
        except httpx.HTTPError:
            return False
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/sources/test_ashby_board.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sources/ashby_board.py tests/unit/sources/test_ashby_board.py
git commit -m "$(cat <<'EOF'
feat(sources): add Ashby board adapter

Public posting endpoint (api.ashbyhq.com/posting-api/job-board/{slug}), no
auth, single-shot. external_id is the jobUrl with query params stripped,
since Ashby's public response doesn't expose a stable numeric id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A4: Source registry

**Files:**
- Modify: `app/sources/__init__.py`
- Test: `tests/unit/sources/test_registry.py` (new)

Single source of truth for which providers exist. The scheduler, slug registry, and resolver all look up `SOURCES[provider]`. Note: `provider_name` of `GreenhouseBoardSource` still returns `"greenhouse_board"`; the registry key is the *post-migration* canonical name (`"greenhouse"`). Track B's migration flips `GreenhouseBoardSource.provider_name` to `"greenhouse"` in the same change set as the database UPDATE — until then, the registry will key Greenhouse under `"greenhouse"` while the adapter still reports `"greenhouse_board"`. This temporary skew is acceptable because nothing in Tracks A1–A4 actually exercises the registry against the live scheduler.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/sources/test_registry.py`:

```python
"""Tests for the source registry."""

from app.sources import SOURCES
from app.sources.ashby_board import AshbyBoardSource
from app.sources.base import JobSource
from app.sources.greenhouse_board import GreenhouseBoardSource
from app.sources.lever_postings import LeverPostingsSource


def test_registry_keys_are_bare_provider_names():
    assert set(SOURCES.keys()) == {"greenhouse", "lever", "ashby"}


def test_registry_values_are_jobsource_instances():
    for value in SOURCES.values():
        assert isinstance(value, JobSource)


def test_registry_maps_to_correct_classes():
    assert isinstance(SOURCES["greenhouse"], GreenhouseBoardSource)
    assert isinstance(SOURCES["lever"], LeverPostingsSource)
    assert isinstance(SOURCES["ashby"], AshbyBoardSource)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/sources/test_registry.py -v`
Expected: FAIL — `SOURCES` doesn't exist yet.

- [ ] **Step 3: Update `app/sources/__init__.py`**

Replace the file with:

```python
"""Source adapter registry.

Provider keys here are canonical: matching `SlugFetch.source`, `Job.source`,
and `Company.provider_slugs` keys, all in their post-migration shape.

Adding a new ATS provider:
  1. Implement a JobSource subclass in app/sources/<provider>.py
  2. Add it to SOURCES below.
  3. The resolver fan-out, scheduler dispatch, and slug-validation flow
     all pick it up automatically.
"""

from app.sources.ashby_board import AshbyBoardSource
from app.sources.base import JobSource
from app.sources.greenhouse_board import GreenhouseBoardSource
from app.sources.lever_postings import LeverPostingsSource

SOURCES: dict[str, JobSource] = {
    "greenhouse": GreenhouseBoardSource(),
    "lever": LeverPostingsSource(),
    "ashby": AshbyBoardSource(),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/sources/ -v`
Expected: PASS — all source tests including the new registry tests.

- [ ] **Step 5: Commit**

```bash
git add app/sources/__init__.py tests/unit/sources/test_registry.py
git commit -m "$(cat <<'EOF'
feat(sources): expose SOURCES registry keyed by canonical provider name

Single dispatch table consumed by the resolver, scheduler, and slug
registry. Greenhouse's adapter still reports source_name 'greenhouse_board'
until Track B migrates the column; the registry key is the post-migration
canonical 'greenhouse' name to match what the rest of the system will use
once the migration lands.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track B — Database, models, and the description rename

Goal: introduce the `companies` table, add `target_company_ids` to profiles, add `Job.company_id`, rename description fields, and migrate `'greenhouse_board' → 'greenhouse'` in `jobs.source` / `slug_fetches.source`. All in one Alembic revision per the spec.

### Task B1: Add Company SQLModel and modify existing models

**Files:**
- Create: `app/models/company.py`
- Modify: `app/models/__init__.py`
- Modify: `app/models/job.py`
- Modify: `app/models/user_profile.py`
- Test: covered by integration tests in B2 (model definitions are exercised when migration runs).

We define the new schema in code first; B2 generates the migration from it.

- [ ] **Step 1: Create `app/models/company.py`**

```python
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    canonical_name: str = Field(sa_column=Column(sa.Text, nullable=False))
    normalized_key: str = Field(sa_column=Column(sa.Text, nullable=False, unique=True, index=True))
    provider_slugs: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    unfollowable: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    resolved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
```

- [ ] **Step 2: Register `Company` in `app/models/__init__.py`**

Add an import line so `alembic/env.py`'s metadata sees the new table:

```python
from app.models.company import Company  # noqa: F401
```

(Place it alongside the other model imports — the existing pattern is `from app.models.<name> import <Class>  # noqa: F401`.)

- [ ] **Step 3: Modify `app/models/job.py`**

Replace the file with:

```python
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str  # greenhouse, lever, ashby (and legacy adzuna/jsearch/remoteok/remotive)
    external_id: str
    title: str
    company_name: str
    company_id: uuid.UUID | None = Field(default=None, foreign_key="companies.id", index=True)
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_raw: str | None = None  # untouched source payload (HTML for greenhouse/lever/ashby)
    description: str | None = None  # canonical markdown, populated by html_cleaner at ingestion
    salary: str | None = None
    contract_type: str | None = None
    apply_url: str
    posted_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    is_active: bool = True

    __table_args__ = (
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
```

- [ ] **Step 4: Modify `app/models/user_profile.py`**

Add the `target_company_ids` field. Locate the `target_company_slugs` line (around line 52) and insert immediately after it:

```python
    target_company_ids: list[uuid.UUID] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )
```

If `from sqlalchemy.dialects.postgresql import ARRAY, JSONB` is already imported (it is), no extra import is needed. Add `from sqlalchemy.dialects.postgresql import UUID as PG_UUID` near the other postgres imports if the inline `sa.dialects.postgresql.UUID(as_uuid=True)` form feels too dense; either works.

Leave `target_company_slugs` in place — the spec keeps it for one release as a rollback safety net. Add a class-level comment above it:

```python
    # DEPRECATED: replaced by target_company_ids. Kept one release for rollback;
    # the follow-up Alembic revision drops this column.
    target_company_slugs: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
```

- [ ] **Step 5: Verify model imports compile**

Run: `uv run python -c "from app.models import job, user_profile, company; print('ok')"`
Expected: `ok` printed (no import errors). The migration runs in B2.

- [ ] **Step 6: Commit**

```bash
git add app/models/company.py app/models/__init__.py app/models/job.py app/models/user_profile.py
git commit -m "$(cat <<'EOF'
feat(models): add Company, target_company_ids, jobs.company_id, description rename

- New Company SQLModel with provider_slugs JSONB, normalized_key UNIQUE,
  unfollowable flag, resolved_at timestamp.
- UserProfile.target_company_ids UUID[] (replaces target_company_slugs JSONB
  one release on; old column kept as rollback safety net).
- Job: rename description_md -> description_raw, description_clean ->
  description; add company_id FK to companies.
- Track B2 generates the corresponding Alembic migration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: Alembic migration (schema + data backfill)

**Files:**
- Create: `alembic/versions/<id>_add_companies_and_rename_description.py`
- Test: `tests/integration/test_migration_companies.py`

One revision: companies table, profile column, jobs column + renames, source-string normalization, data backfill.

- [ ] **Step 1: Generate the migration scaffold**

Run: `make migrate ARGS="revision --autogenerate -m add_companies_and_rename_description"`
Expected: a new file under `alembic/versions/` is created. Note its filename for later commits.

(The wrapper `scripts/alembic_safe.py` blocks the autogenerate against non-local hosts unless `I_KNOW_ITS_PROD=1` is set; running it locally against the docker-compose Postgres is fine.)

- [ ] **Step 2: Hand-edit the generated migration**

Open the new file. Replace its entire body with:

```python
"""add companies, target_company_ids, jobs.company_id, description rename, source normalization

Revision ID: <generated>
Revises: <previous>
Create Date: 2026-05-08 ...
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers — keep whatever autogenerate produced.
revision = "<generated>"
down_revision = "<previous>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. companies table
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("provider_slugs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unfollowable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("normalized_key", name="uq_companies_normalized_key"),
    )
    op.create_index("ix_companies_normalized_key", "companies", ["normalized_key"], unique=False)
    op.create_index(
        "ix_companies_provider_slugs",
        "companies",
        ["provider_slugs"],
        postgresql_using="gin",
    )

    # 2. user_profiles.target_company_ids
    op.add_column(
        "user_profiles",
        sa.Column(
            "target_company_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )

    # 3. jobs.company_id + indexes
    op.add_column(
        "jobs",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_company_id_companies",
        "jobs",
        "companies",
        ["company_id"],
        ["id"],
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"], unique=False)

    # 4. description column renames
    op.alter_column("jobs", "description_md", new_column_name="description_raw")
    op.alter_column("jobs", "description_clean", new_column_name="description")

    # 5. provider name normalization
    op.execute("UPDATE jobs SET source = 'greenhouse' WHERE source = 'greenhouse_board'")
    op.execute("UPDATE slug_fetches SET source = 'greenhouse' WHERE source = 'greenhouse_board'")

    # 6. data backfill — Company rows from existing greenhouse slugs
    op.execute("""
        INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs, resolved_at, created_at)
        SELECT
            gen_random_uuid(),
            initcap(replace(slug, '-', ' ')),
            slug,
            jsonb_build_object('greenhouse', slug),
            NOW(),
            NOW()
        FROM (
            SELECT DISTINCT jsonb_array_elements_text(target_company_slugs->'greenhouse') AS slug
            FROM user_profiles
            WHERE jsonb_typeof(target_company_slugs->'greenhouse') = 'array'
        ) s
        WHERE slug IS NOT NULL AND slug <> ''
        ON CONFLICT (normalized_key) DO NOTHING
    """)

    # 7. populate target_company_ids on each profile
    op.execute("""
        UPDATE user_profiles up
        SET target_company_ids = COALESCE((
            SELECT array_agg(c.id)
            FROM jsonb_array_elements_text(up.target_company_slugs->'greenhouse') AS slug
            JOIN companies c ON c.provider_slugs->>'greenhouse' = slug
        ), '{}')
    """)

    # 8. backfill jobs.company_id from existing greenhouse jobs
    op.execute("""
        UPDATE jobs j
        SET company_id = c.id
        FROM companies c
        WHERE j.source = 'greenhouse'
          AND c.provider_slugs->>'greenhouse' IS NOT NULL
          AND c.canonical_name = j.company_name
    """)


def downgrade() -> None:
    # Reverse everything in inverse order. Lossy: dropped Company rows + renames
    # back. Acceptable since we hold target_company_slugs as rollback safety net.
    op.execute("UPDATE jobs SET company_id = NULL")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_constraint("fk_jobs_company_id_companies", "jobs", type_="foreignkey")
    op.drop_column("jobs", "company_id")

    op.alter_column("jobs", "description", new_column_name="description_clean")
    op.alter_column("jobs", "description_raw", new_column_name="description_md")

    op.execute("UPDATE jobs SET source = 'greenhouse_board' WHERE source = 'greenhouse'")
    op.execute("UPDATE slug_fetches SET source = 'greenhouse_board' WHERE source = 'greenhouse'")

    op.drop_column("user_profiles", "target_company_ids")

    op.drop_index("ix_companies_provider_slugs", table_name="companies", postgresql_using="gin")
    op.drop_index("ix_companies_normalized_key", table_name="companies")
    op.drop_table("companies")
```

Replace `<generated>` and `<previous>` placeholders with whatever the autogenerated file already has — those are the revision identifiers.

- [ ] **Step 3: Run the migration locally**

Run: `make migrate ARGS="upgrade head"`
Expected: migration applies cleanly. If a revision ID conflict occurs, run `make migrate ARGS="heads"` to inspect — autogenerate occasionally picks a parent that's not the current head; fix the `down_revision` if so.

- [ ] **Step 4: Write the migration integration test**

Create `tests/integration/test_migration_companies.py`:

```python
"""Integration test: migration creates Company rows and target_company_ids
from existing target_company_slugs JSON, drops dead lever/ashby entries."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_backfills_companies_from_greenhouse_slugs(db_session):
    """Seed pre-migration profile shape, run upgrade, assert company rows
    materialize and target_company_ids populates."""
    # NOTE: this test runs against a testcontainers Postgres where Alembic
    # has already upgraded to head (per conftest), so we simulate the data
    # state by inserting a profile via SQL with both old and new columns
    # populated, then verify the new shape is queryable.
    await db_session.execute(text("""
        INSERT INTO users (id, email, created_at, updated_at)
        VALUES (:uid, 'mig@example.com', NOW(), NOW())
    """), {"uid": str(uuid.uuid4())})
    await db_session.commit()

    user_id = (await db_session.execute(text(
        "SELECT id FROM users WHERE email = 'mig@example.com'"
    ))).scalar_one()

    await db_session.execute(text("""
        INSERT INTO user_profiles (id, user_id, target_company_slugs, created_at, updated_at)
        VALUES (:pid, :uid, :slugs, NOW(), NOW())
    """), {
        "pid": str(uuid.uuid4()),
        "uid": str(user_id),
        "slugs": json.dumps({"greenhouse": ["stripe", "linear"], "lever": ["dead-entry"]}),
    })
    await db_session.commit()

    # Re-run the data-backfill blocks from the migration so the test is
    # deterministic against a fixture profile inserted after the original
    # upgrade ran. This simulates "what would happen if a profile with this
    # shape existed at upgrade time."
    await db_session.execute(text("""
        INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs, resolved_at, created_at)
        SELECT gen_random_uuid(), initcap(replace(slug, '-', ' ')), slug,
               jsonb_build_object('greenhouse', slug), NOW(), NOW()
        FROM (
            SELECT DISTINCT jsonb_array_elements_text(target_company_slugs->'greenhouse') AS slug
            FROM user_profiles WHERE jsonb_typeof(target_company_slugs->'greenhouse') = 'array'
        ) s
        WHERE slug IS NOT NULL AND slug <> ''
        ON CONFLICT (normalized_key) DO NOTHING
    """))
    await db_session.execute(text("""
        UPDATE user_profiles up
        SET target_company_ids = COALESCE((
            SELECT array_agg(c.id)
            FROM jsonb_array_elements_text(up.target_company_slugs->'greenhouse') AS slug
            JOIN companies c ON c.provider_slugs->>'greenhouse' = slug
        ), '{}')
    """))
    await db_session.commit()

    # Two Company rows: stripe and linear.
    rows = (await db_session.execute(text(
        "SELECT canonical_name, normalized_key, provider_slugs FROM companies "
        "WHERE normalized_key IN ('stripe', 'linear') ORDER BY normalized_key"
    ))).all()
    assert len(rows) == 2
    assert rows[0].normalized_key == "linear"
    assert rows[0].canonical_name == "Linear"
    assert rows[0].provider_slugs == {"greenhouse": "linear"}
    assert rows[1].normalized_key == "stripe"

    # target_company_ids has both UUIDs.
    profile_ids = (await db_session.execute(text(
        "SELECT target_company_ids FROM user_profiles WHERE user_id = :uid"
    ), {"uid": str(user_id)})).scalar_one()
    assert len(profile_ids) == 2

    # Lever 'dead-entry' did NOT create a Company row (only greenhouse seeds did).
    lever_rows = (await db_session.execute(text(
        "SELECT id FROM companies WHERE provider_slugs ? 'lever'"
    ))).all()
    assert len(lever_rows) == 0


@pytest.mark.asyncio
async def test_migration_backfills_jobs_company_id(db_session):
    """jobs.company_id populates for greenhouse jobs whose company_name
    matches a Company.canonical_name."""
    company_id = uuid.uuid4()
    await db_session.execute(text("""
        INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs, resolved_at, created_at)
        VALUES (:cid, 'Stripe', 'stripe-fixture', :slugs, NOW(), NOW())
    """), {"cid": str(company_id), "slugs": json.dumps({"greenhouse": "stripe-fixture"})})

    await db_session.execute(text("""
        INSERT INTO jobs (id, source, external_id, title, company_name, apply_url, fetched_at, is_active)
        VALUES (:jid, 'greenhouse', 'job-1', 'SWE', 'Stripe', 'https://example.com/1', NOW(), true)
    """), {"jid": str(uuid.uuid4())})
    await db_session.commit()

    # Re-run the backfill block.
    await db_session.execute(text("""
        UPDATE jobs j
        SET company_id = c.id
        FROM companies c
        WHERE j.source = 'greenhouse'
          AND c.provider_slugs->>'greenhouse' IS NOT NULL
          AND c.canonical_name = j.company_name
    """))
    await db_session.commit()

    backfilled = (await db_session.execute(text(
        "SELECT company_id FROM jobs WHERE external_id = 'job-1'"
    ))).scalar_one()
    assert backfilled == company_id
```

- [ ] **Step 5: Run the integration test**

Run: `uv run pytest tests/integration/test_migration_companies.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/<id>_add_companies_and_rename_description.py tests/integration/test_migration_companies.py
git commit -m "$(cat <<'EOF'
feat(migration): companies table, target_company_ids, jobs.company_id, description rename

One Alembic revision:
  - Create companies table with provider_slugs JSONB, normalized_key UNIQUE,
    GIN index on provider_slugs.
  - Add user_profiles.target_company_ids UUID[].
  - Add jobs.company_id FK + index.
  - Rename jobs.description_md -> description_raw, description_clean ->
    description.
  - UPDATE jobs.source / slug_fetches.source 'greenhouse_board' ->
    'greenhouse' (canonical bare provider name).
  - Backfill: Company rows per unique greenhouse slug, populate
    target_company_ids per profile, populate jobs.company_id where
    canonical_name matches.

Lever/Ashby entries in target_company_slugs are not migrated — they were
dead-input the backend never read. Users re-add by name post-deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: Update job_service for new field names + flip Greenhouse `provider_name`

**Files:**
- Modify: `app/services/job_service.py`
- Modify: `app/sources/greenhouse_board.py`
- Modify: any tests that hardcode `source="greenhouse_board"` or `description_md=` / `description_clean=`.

Now that the migration has flipped DB values to `"greenhouse"`, the adapter must report that name and the service must read/write the new column names.

- [ ] **Step 1: Find and update tests with stale strings**

Run: `rg -nl 'greenhouse_board|description_md|description_clean' tests/ app/ frontend/src/` and inspect.

For each match in `tests/` and `app/` (excluding the migration file we just wrote — that one intentionally references the old names in `op.alter_column` / `UPDATE … WHERE source = 'greenhouse_board'`):
- `"greenhouse_board"` → `"greenhouse"` in test fixtures and assertions.
- `description_md=` → `description_raw=` in `JobData(...)` constructions.
- `description_clean=` → `description=` in `Job(...)` and assertions.

- [ ] **Step 2: Update `app/services/job_service.py`**

Replace the file with:

```python
"""Job CRUD and staleness logic."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.job import Job
from app.services.html_cleaner import clean_html_to_markdown
from app.sources.base import JobData


async def upsert_job(job_data: JobData, source: str, session: AsyncSession) -> tuple[Job, bool]:
    """
    Insert or update a job. Returns (job, created).
    On conflict (source + external_id): update title, descriptions, is_active, fetched_at.
    description (canonical markdown) is recomputed from description_raw on every write.
    """
    result = await session.execute(
        select(Job).where(
            Job.source == source,
            Job.external_id == job_data.external_id,
        )
    )
    existing = result.scalar_one_or_none()

    cleaned = clean_html_to_markdown(job_data.description_raw)

    if existing:
        existing.title = job_data.title
        existing.company_name = job_data.company_name
        existing.description_raw = job_data.description_raw
        existing.description = cleaned
        existing.salary = job_data.salary
        existing.contract_type = job_data.contract_type
        existing.apply_url = job_data.apply_url
        existing.location = job_data.location
        existing.workplace_type = job_data.workplace_type
        existing.is_active = True
        existing.fetched_at = datetime.now(UTC)
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing, False

    job = Job(
        source=source,
        external_id=job_data.external_id,
        title=job_data.title,
        company_name=job_data.company_name,
        location=job_data.location,
        workplace_type=job_data.workplace_type,
        description_raw=job_data.description_raw,
        description=cleaned,
        salary=job_data.salary,
        contract_type=job_data.contract_type,
        apply_url=job_data.apply_url,
        posted_at=job_data.posted_at,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, True


async def get_active_jobs(
    session: AsyncSession,
    source: str | None = None,
    workplace_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    q = select(Job).where(Job.is_active.is_(True))
    if source:
        q = q.where(Job.source == source)
    if workplace_type:
        q = q.where(Job.workplace_type == workplace_type)
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def mark_stale_jobs(stale_after_days: int, session: AsyncSession) -> int:
    """Mark jobs as inactive if not refreshed within stale_after_days. Returns count."""
    cutoff = datetime.now(UTC) - timedelta(days=stale_after_days)
    result = await session.execute(
        select(Job).where(
            Job.is_active.is_(True),
            Job.fetched_at < cutoff,
        )
    )
    jobs = list(result.scalars().all())
    for job in jobs:
        job.is_active = False
        session.add(job)
    if jobs:
        await session.commit()
    return len(jobs)
```

- [ ] **Step 3: Flip Greenhouse adapter `provider_name` to "greenhouse"**

Edit `app/sources/greenhouse_board.py` — change:

```python
    @property
    def provider_name(self) -> str:
        return "greenhouse_board"
```

to:

```python
    @property
    def provider_name(self) -> str:
        return "greenhouse"
```

- [ ] **Step 4: Update the Track A1 base test that asserted the old name**

Edit `tests/unit/sources/test_base.py` — change:

```python
    assert src.provider_name == "greenhouse_board"
```

to:

```python
    assert src.provider_name == "greenhouse"
```

- [ ] **Step 5: Run all unit + integration tests**

Run: `uv run pytest tests/unit/ tests/integration/ -v`
Expected: PASS. Anything red is a missed `description_md` / `description_clean` / `greenhouse_board` literal — fix mechanically and re-run.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(jobs): use description_raw/description, flip greenhouse provider name

job_service reads/writes description_raw + description (rename of
description_md + description_clean). Greenhouse adapter's provider_name now
returns 'greenhouse' to match the post-migration source column. Test
fixtures across the suite renamed 'greenhouse_board' -> 'greenhouse'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track C — Resolver and HTTP endpoint

### Task C1: `company_resolver.resolve()` service

**Files:**
- Create: `app/services/company_resolver.py`
- Test: `tests/unit/services/test_company_resolver.py`

Cache-first lookup, parallel `validate()` fan-out, persist with `ON CONFLICT (normalized_key) DO NOTHING RETURNING`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/test_company_resolver.py`:

```python
"""Tests for company_resolver.resolve()."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models.company import Company
from app.services import company_resolver


@pytest.mark.asyncio
async def test_normalize_strips_case_and_whitespace_and_hyphenates():
    assert company_resolver._normalize("  Linear  ") == "linear"
    assert company_resolver._normalize("Meta Platforms") == "meta-platforms"
    assert company_resolver._normalize("ByteDance") == "bytedance"
    assert company_resolver._normalize("Acme   Corp") == "acme-corp"


@pytest.mark.asyncio
async def test_resolve_cache_hit_returns_existing_company(db_session):
    existing = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.commit()

    with patch.object(company_resolver, "_fan_out", new=AsyncMock()) as fan_out:
        result = await company_resolver.resolve("Linear", db_session)

    assert result is not None
    assert result.id == existing.id
    fan_out.assert_not_called()  # cache hit short-circuits


@pytest.mark.asyncio
async def test_resolve_single_provider_match_persists_and_returns(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"ashby": True}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Linear", db_session)

    assert result is not None
    assert result.canonical_name == "Linear"
    assert result.normalized_key == "linear"
    assert result.provider_slugs == {"ashby": "linear"}


@pytest.mark.asyncio
async def test_resolve_multi_provider_match_stores_all(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": True, "ashby": True}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert set(result.provider_slugs.keys()) == {"greenhouse", "ashby"}


@pytest.mark.asyncio
async def test_resolve_no_match_returns_none(db_session):
    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": False, "lever": False, "ashby": False}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("nonexistent-co", db_session)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_fanout_timeout_raises(db_session):
    """The endpoint distinguishes timeout (503) from no-match (404), so the
    resolver raises FanoutTimeoutError on the timeout path rather than
    returning None."""
    async def slow_fan_out(slug, *, timeout):
        raise asyncio.TimeoutError

    with patch.object(company_resolver, "_fan_out", new=slow_fan_out):
        with pytest.raises(company_resolver.FanoutTimeoutError):
            await company_resolver.resolve("Linear", db_session)


@pytest.mark.asyncio
async def test_resolve_returns_existing_row_when_normalized_key_matches(db_session):
    """When the row already exists, the cache lookup returns it without
    fanning out. This also exercises the same path the concurrent-insert
    'loser' would take after re-SELECTing on ON CONFLICT DO NOTHING."""
    async def fake_fan_out(slug, *, timeout):
        return {"greenhouse": True}

    pre_existing = Company(
        canonical_name="Stripe",
        normalized_key="stripe",
        provider_slugs={"greenhouse": "stripe"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(pre_existing)
    await db_session.commit()

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert result is not None
    assert result.id == pre_existing.id


@pytest.mark.asyncio
async def test_resolve_persists_partial_match_with_failed_provider_logged(db_session, caplog):
    """If one provider 200s and others 5xx: persist the confirmed provider,
    log company_resolver.partial_match for ops awareness."""
    async def fake_fan_out(slug, *, timeout):
        # _fan_out returns {provider: True/False/'error'} — 'error' counts
        # as a transient failure that should be logged but not block persist.
        return {"greenhouse": True, "lever": "error", "ashby": False}

    with patch.object(company_resolver, "_fan_out", new=fake_fan_out):
        result = await company_resolver.resolve("Stripe", db_session)

    assert result is not None
    assert "greenhouse" in result.provider_slugs
    assert "lever" not in result.provider_slugs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/services/test_company_resolver.py -v`
Expected: FAIL — `company_resolver` module doesn't exist.

- [ ] **Step 3: Implement `app/services/company_resolver.py`**

```python
"""Company resolution service.

Algorithm:
  1. Normalize input (lowercase, strip, hyphenate whitespace).
  2. Cache lookup by normalized_key.
  3. On miss: parallel validate() across all SOURCES with a wall timeout.
  4. Persist confirmed providers via ON CONFLICT (normalized_key) DO NOTHING
     RETURNING; on no-row-returned, re-SELECT (concurrent-resolve race).
  5. Return Company or None.
"""

import asyncio
import re
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.sources import SOURCES

DEFAULT_FANOUT_TIMEOUT = 3.0  # seconds

log = structlog.get_logger()

ProbeResult = bool | Literal["error"]


class FanoutTimeoutError(Exception):
    """Raised when validate() across all SOURCES exceeds the wall timeout.

    Distinguishes from 'no match' (resolve returns None): the API layer
    converts this to 503 so the user can retry, vs 404 for confirmed miss.
    """


def _normalize(text: str) -> str:
    """Trim, lowercase, collapse internal whitespace runs to single hyphens."""
    s = text.strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s


async def _fan_out(slug: str, *, timeout: float) -> dict[str, ProbeResult]:
    """Run validate() across every adapter in parallel with a shared wall timeout.

    Returns a dict mapping provider_name -> True (200), False (404), or
    'error' (5xx, network, malformed). Raises asyncio.TimeoutError if the
    aggregate wall exceeds `timeout`.
    """
    async def probe(provider: str, src):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                ok = await src.validate(slug, client=client)
            return provider, bool(ok)
        except Exception:
            return provider, "error"

    coros = [probe(p, s) for p, s in SOURCES.items()]
    results = await asyncio.wait_for(asyncio.gather(*coros), timeout=timeout)
    return {p: r for p, r in results}


async def resolve(input_text: str, session: AsyncSession) -> Company | None:
    """Resolve a free-text company input to a Company row, fan-out + cache.

    Returns None on no-match or fan-out timeout.
    """
    normalized = _normalize(input_text)
    if not normalized:
        return None

    # Cache lookup
    existing = (
        await session.execute(
            select(Company).where(Company.normalized_key == normalized)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await log.adebug("company_resolver.cache_hit", normalized=normalized)
        return existing

    # Fan out
    try:
        results = await _fan_out(normalized, timeout=DEFAULT_FANOUT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        await log.awarning("company_resolver.fanout_timeout", normalized=normalized)
        raise FanoutTimeoutError(normalized) from exc

    confirmed = {p: normalized for p, r in results.items() if r is True}
    failed = [p for p, r in results.items() if r == "error"]

    if not confirmed:
        await log.ainfo("company_resolver.no_match", normalized=normalized, failed=failed)
        return None

    if failed:
        await log.awarning(
            "company_resolver.partial_match",
            normalized=normalized,
            confirmed=list(confirmed),
            failed=failed,
        )

    canonical = " ".join(w.capitalize() for w in normalized.split("-"))
    stmt = (
        insert(Company)
        .values(
            canonical_name=canonical,
            normalized_key=normalized,
            provider_slugs=confirmed,
            resolved_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["normalized_key"])
        .returning(Company.id)
    )
    result = await session.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    await session.commit()

    if inserted_id is None:
        # Concurrent insert won the race — re-SELECT.
        existing = (
            await session.execute(
                select(Company).where(Company.normalized_key == normalized)
            )
        ).scalar_one()
        await log.ainfo("company_resolver.match_concurrent", normalized=normalized)
        return existing

    company = (
        await session.execute(select(Company).where(Company.id == inserted_id))
    ).scalar_one()
    await log.ainfo(
        "company_resolver.match",
        normalized=normalized,
        providers=list(confirmed),
    )
    return company
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/services/test_company_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/company_resolver.py tests/unit/services/test_company_resolver.py
git commit -m "$(cat <<'EOF'
feat(services): company_resolver — fan-out + cache resolution

POST-cache lookup, parallel validate() across SOURCES with 3s wall, persist
confirmed providers via ON CONFLICT DO NOTHING RETURNING. Concurrent-resolve
race handled via re-SELECT on no-row-returned. Partial-match (some providers
5xx) persists the confirmed set and logs failures for follow-up retry.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: `POST /api/companies/resolve` endpoint

**Files:**
- Create: `app/api/companies.py`
- Modify: `app/main.py`
- Test: `tests/unit/api/test_companies_resolve.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_companies_resolve.py`:

```python
"""Tests for POST /api/companies/resolve."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_resolve_success(client, auth_profile):
    from datetime import UTC, datetime
    from app.models.company import Company

    fake = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear"},
        resolved_at=datetime.now(UTC),
    )
    with patch("app.api.companies.company_resolver.resolve", new=AsyncMock(return_value=fake)):
        resp = await client.post("/api/companies/resolve", json={"name": "Linear"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["canonical_name"] == "Linear"
    assert set(body["company"]["providers"]) == {"ashby"}
    assert "id" in body["company"]


@pytest.mark.asyncio
async def test_resolve_not_found(client, auth_profile):
    with patch("app.api.companies.company_resolver.resolve", new=AsyncMock(return_value=None)):
        resp = await client.post("/api/companies/resolve", json={"name": "nope-co"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_timeout_returns_503(client, auth_profile):
    from app.services.company_resolver import FanoutTimeoutError

    with patch(
        "app.api.companies.company_resolver.resolve",
        new=AsyncMock(side_effect=FanoutTimeoutError("linear")),
    ):
        resp = await client.post("/api/companies/resolve", json={"name": "Linear"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_resolve_empty_name_400(client, auth_profile):
    resp = await client.post("/api/companies/resolve", json={"name": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_unauthenticated_returns_401(client):
    resp = await client.post("/api/companies/resolve", json={"name": "Linear"})
    assert resp.status_code in (401, 403)
```

(`client` and `auth_profile` are existing fixtures used by other API tests under `tests/unit/api/` — reuse the same imports/conftest the existing test files use; if the project structure puts API tests under `tests/integration/`, follow that convention.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/api/test_companies_resolve.py -v`
Expected: FAIL — endpoint doesn't exist (404 on the route itself).

- [ ] **Step 3: Implement `app/api/companies.py`**

```python
"""Company resolution endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.user_profile import UserProfile
from app.services import company_resolver

router = APIRouter(prefix="/api/companies", tags=["companies"])


class ResolveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ResolveResponse(BaseModel):
    company: dict


@router.post("/resolve")
async def resolve_company(
    body: ResolveRequest,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Resolve a free-text company name to a Company row via fan-out across
    every supported ATS provider.

    Returns:
      200 — confirmed match
      400 — empty name
      404 — every provider returned 404 (confirmed miss)
      503 — fan-out timed out (transient; user retries)
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        company = await company_resolver.resolve(body.name, session)
    except company_resolver.FanoutTimeoutError:
        raise HTTPException(status_code=503, detail="couldn't reach our boards right now")
    if company is None:
        raise HTTPException(status_code=404, detail="company not found on any supported board")
    return {
        "company": {
            "id": str(company.id),
            "canonical_name": company.canonical_name,
            "providers": list(company.provider_slugs.keys()),
        }
    }
```

- [ ] **Step 4: Register the router in `app/main.py`**

Locate the existing `app.include_router(...)` calls and add:

```python
from app.api.companies import router as companies_router
...
app.include_router(companies_router)
```

(Place near the other `include_router` calls — follow the existing style.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/api/test_companies_resolve.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/companies.py app/main.py tests/unit/api/test_companies_resolve.py
git commit -m "$(cat <<'EOF'
feat(api): POST /api/companies/resolve

Frontend's single 'Add a company' input posts here; resolver fans out across
greenhouse/lever/ashby and persists the matching Company. 404 on full miss,
400 on empty input.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track D — Service & scheduler integration

### Task D1: Generalize `slug_registry_service.validate_slug` to dispatch via `SOURCES`

**Files:**
- Modify: `app/services/slug_registry_service.py`
- Test: extend or create `tests/unit/services/test_slug_registry_service.py`

- [ ] **Step 1: Write the failing tests**

Create or extend `tests/unit/services/test_slug_registry_service.py`:

```python
"""Tests for slug_registry_service.validate_slug dispatch."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import slug_registry_service


@pytest.mark.asyncio
async def test_validate_slug_dispatches_to_lever(db_session):
    fake_lever = AsyncMock()
    fake_lever.validate = AsyncMock(return_value=True)
    with patch.dict("app.services.slug_registry_service.SOURCES", {"lever": fake_lever}, clear=False):
        ok = await slug_registry_service.validate_slug("lever", "acme", db_session)
    assert ok is True
    fake_lever.validate.assert_awaited_once_with("acme")


@pytest.mark.asyncio
async def test_validate_slug_unknown_provider_raises(db_session):
    with pytest.raises(ValueError, match="unknown provider"):
        await slug_registry_service.validate_slug("myspace", "acme", db_session)


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404(db_session):
    fake = AsyncMock()
    fake.validate = AsyncMock(return_value=False)
    with patch.dict("app.services.slug_registry_service.SOURCES", {"ashby": fake}, clear=False):
        ok = await slug_registry_service.validate_slug("ashby", "missing", db_session)
    assert ok is False
```

- [ ] **Step 2: Modify `app/services/slug_registry_service.py`**

Replace lines 19 and 32–51 (the `from app.sources.greenhouse_board import GreenhouseBoardSource` import and the `validate_slug` function) with:

```python
from app.sources import SOURCES
```

…and rewrite `validate_slug`:

```python
async def validate_slug(source: str, slug: str, session: AsyncSession) -> bool:
    """Returns True if the slug exists on the given provider's board.

    Looks up the adapter in app.sources.SOURCES; raises ValueError on unknown
    provider. On True, upserts a SlugFetch row with last_status='ok'.
    """
    adapter = SOURCES.get(source)
    if adapter is None:
        raise ValueError(f"unknown provider: {source}")
    ok = await adapter.validate(slug)
    if not ok:
        return False
    stmt = (
        insert(SlugFetch)
        .values(source=source, slug=slug, last_status="ok")
        .on_conflict_do_update(
            index_elements=["source", "slug"],
            set_={"last_status": "ok"},
        )
    )
    await session.execute(stmt)
    await session.commit()
    return True
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/services/test_slug_registry_service.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/services/slug_registry_service.py tests/unit/services/test_slug_registry_service.py
git commit -m "$(cat <<'EOF'
refactor(slug_registry): generalize validate_slug to dispatch via SOURCES

Drop the 'only supports greenhouse_board' raise. Adapter lookup goes through
app.sources.SOURCES so adding a new provider is a registry update only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task D2: Rewrite `enqueue_stale` to walk `Company.provider_slugs`

**Files:**
- Modify: `app/services/slug_registry_service.py`
- Test: extend `tests/unit/services/test_slug_registry_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/test_slug_registry_service.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile


@pytest.mark.asyncio
async def test_enqueue_stale_walks_company_provider_slugs(db_session, profile_factory):
    """For each company in target_company_ids, every provider_slug entry that's
    stale or missing gets a SlugFetch row queued."""
    company = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear", "greenhouse": "linear"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = await profile_factory(target_company_ids=[company.id])

    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert sorted(queued) == ["linear", "linear"]  # one per provider

    rows = (await db_session.execute(
        select(SlugFetch).where(SlugFetch.slug == "linear")
    )).scalars().all()
    sources = sorted(r.source for r in rows)
    assert sources == ["ashby", "greenhouse"]
    assert all(r.queued_at is not None for r in rows)


@pytest.mark.asyncio
async def test_enqueue_stale_skips_unfollowable_companies(db_session, profile_factory):
    company = Company(
        canonical_name="DefunctCo",
        normalized_key="defunctco",
        provider_slugs={},
        unfollowable=True,
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = await profile_factory(target_company_ids=[company.id])
    queued = await slug_registry_service.enqueue_stale(profile, db_session)
    assert queued == []
```

(If `profile_factory` doesn't exist as a fixture, add a minimal version in `tests/conftest.py` — or seed the profile inline with `db_session.add(UserProfile(...))`.)

- [ ] **Step 2: Replace `enqueue_stale` in `app/services/slug_registry_service.py`**

Add this import near the top:

```python
from app.models.company import Company
```

Replace the existing `enqueue_stale` function with:

```python
async def enqueue_stale(profile, session: AsyncSession, *, ttl_hours: int = 6) -> list[str]:
    """For each (provider, slug) pair in the user's followed Company rows,
    queue a SlugFetch if its last_fetched_at is NULL or older than now-ttl_hours.

    Returns the list of slugs newly queued (one entry per provider+slug pair;
    duplicates allowed — same slug under two providers counts twice).
    """
    company_ids = list(profile.target_company_ids or [])
    if not company_ids:
        return []
    companies = (await session.execute(
        select(Company).where(Company.id.in_(company_ids), Company.unfollowable.is_(False))
    )).scalars().all()
    if not companies:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    queued: list[str] = []
    for company in companies:
        for provider, slug in (company.provider_slugs or {}).items():
            row = await get(provider, slug, session)
            if row is None:
                row = SlugFetch(source=provider, slug=slug, queued_at=datetime.now(UTC))
                session.add(row)
                queued.append(slug)
                continue
            if row.is_invalid:
                continue
            already_queued = row.queued_at is not None
            is_stale = row.last_fetched_at is None or row.last_fetched_at < cutoff
            if is_stale and not already_queued:
                row.queued_at = datetime.now(UTC)
                queued.append(slug)
    await session.commit()
    return queued
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/services/test_slug_registry_service.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/services/slug_registry_service.py tests/unit/services/test_slug_registry_service.py
git commit -m "$(cat <<'EOF'
refactor(slug_registry): enqueue_stale walks Company.provider_slugs

Profile interest is now expressed as target_company_ids (FKs to Company).
For each company, queue every (provider, slug) pair that's stale or missing.
Skips companies marked unfollowable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task D3: Generalize scheduler `run_sync_queue` dispatch

**Files:**
- Modify: `app/scheduler/tasks.py`
- Test: existing scheduler tests; extend if needed.

The scheduler currently hardcodes `GreenhouseBoardSource()`. Switch to `SOURCES[row.source]`.

- [ ] **Step 1: Write a failing test**

Add to `tests/integration/test_scheduler_run_sync_queue.py` (create if not present):

```python
"""Integration tests for run_sync_queue dispatch."""

from unittest.mock import AsyncMock, patch

import pytest

from app.scheduler.tasks import run_sync_queue


@pytest.mark.asyncio
async def test_run_sync_queue_dispatches_per_provider(db_session, db_session_factory):
    """Each claimed SlugFetch is fed to the matching adapter in SOURCES,
    keyed by row.source."""
    from datetime import UTC, datetime

    from app.models.slug_fetch import SlugFetch

    fake_greenhouse = AsyncMock()
    fake_greenhouse.fetch_jobs = AsyncMock(return_value=[])
    fake_lever = AsyncMock()
    fake_lever.fetch_jobs = AsyncMock(return_value=[])

    # Seed two queued SlugFetches, one per provider.
    db_session.add(SlugFetch(
        source="greenhouse", slug="stripe", queued_at=datetime.now(UTC)
    ))
    db_session.add(SlugFetch(
        source="lever", slug="acme", queued_at=datetime.now(UTC)
    ))
    await db_session.commit()

    with patch.dict(
        "app.scheduler.tasks.SOURCES",
        {"greenhouse": fake_greenhouse, "lever": fake_lever},
        clear=False,
    ):
        await run_sync_queue(deadline_seconds=5, max_slugs=10)

    fake_greenhouse.fetch_jobs.assert_awaited()
    fake_lever.fetch_jobs.assert_awaited()
```

(`db_session` and `db_session_factory` are existing fixtures used by other integration tests; if the conftest names them differently, swap to whatever provides an `AsyncSession` and a session factory.)

- [ ] **Step 2: Modify `app/scheduler/tasks.py::run_sync_queue`**

Around lines 195-225, change:

```python
    from app.sources.greenhouse_board import (
        DEFAULT_TIMEOUT,
        GreenhouseBoardSource,
        InvalidSlugError,
        TransientFetchError,
    )
    ...
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        source = GreenhouseBoardSource()
```

to:

```python
    from app.sources import SOURCES
    from app.sources.base import InvalidSlugError, TransientFetchError
    from app.sources.greenhouse_board import DEFAULT_TIMEOUT  # shared timeout constant
    ...
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        sem = asyncio.Semaphore(8)

        async def _one(row):
            adapter = SOURCES.get(row.source)
            if adapter is None:
                await log.aerror(
                    "slug_fetch.unknown_provider",
                    source=row.source,
                    slug=row.slug,
                )
                return
            ...
            jobs = await adapter.fetch_jobs(row.slug, since=since, client=client)
```

(The `_one` closure body stays the same except for the adapter swap. Keep `since`, `mark_fetched`, and `match_queue_service.enqueue_for_interested_profiles` calls intact.)

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/integration/test_scheduler_run_sync_queue.py -v`
Expected: PASS. If the file doesn't exist yet, the failing-test step asks the engineer to model after the closest existing scheduler test.

- [ ] **Step 4: Commit**

```bash
git add app/scheduler/tasks.py tests/integration/test_scheduler_run_sync_queue.py
git commit -m "$(cat <<'EOF'
refactor(scheduler): run_sync_queue dispatches via SOURCES[row.source]

Replaces the hardcoded GreenhouseBoardSource() with a registry lookup.
Unknown provider on a SlugFetch row is logged and skipped (no crash).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task D4: Update `job_sync_service` to read `target_company_ids`

**Files:**
- Modify: `app/services/job_sync_service.py`
- Test: extend `tests/unit/services/test_job_sync_service.py` (or wherever the existing tests live).

- [ ] **Step 1: Replace `_prune_invalid_slugs` with `_prune_invalid_provider_slugs`**

Update `app/services/job_sync_service.py`. Replace the existing `_prune_invalid_slugs` and `prune_and_enqueue` functions with:

```python
from app.models.company import Company
from sqlmodel import select


async def _prune_invalid_provider_slugs(profile: UserProfile, session: AsyncSession) -> list[str]:
    """For each Company the profile follows, drop any provider entry whose
    SlugFetch is marked is_invalid. If a Company ends up with zero providers,
    flag it unfollowable. Returns the list of (provider:slug) strings pruned."""
    company_ids = list(profile.target_company_ids or [])
    if not company_ids:
        return []
    companies = (await session.execute(
        select(Company).where(Company.id.in_(company_ids))
    )).scalars().all()
    if not companies:
        return []

    from sqlalchemy import or_, and_

    pruned: list[str] = []
    for company in companies:
        slugs = company.provider_slugs or {}
        if not slugs:
            continue
        pair_clauses = [
            and_(SlugFetch.source == p, SlugFetch.slug == s)
            for p, s in slugs.items()
        ]
        invalid_pairs = (await session.execute(
            select(SlugFetch).where(
                SlugFetch.is_invalid.is_(True),
                or_(*pair_clauses),
            )
        )).scalars().all()
        invalid_keys = {(r.source, r.slug) for r in invalid_pairs}
        if not invalid_keys:
            continue
        cleaned = {p: s for p, s in slugs.items() if (p, s) not in invalid_keys}
        for p, s in invalid_keys:
            pruned.append(f"{p}:{s}")
        company.provider_slugs = cleaned
        if not cleaned:
            company.unfollowable = True
            await log.awarning("company.unfollowable", company_id=str(company.id))
        session.add(company)
    if pruned:
        await session.commit()
    return sorted(pruned)


async def prune_and_enqueue(profile: UserProfile, session: AsyncSession) -> dict:
    """Cron-safe profile sync: seed defaults + prune invalid (provider, slug)
    pairs from followed Companies + enqueue stale + update last_sync_*. No LLM,
    no synchronous scoring. Returns the same summary shape as `sync_profile`
    with `matched_now=0`."""
    seeded = seed_defaults_if_empty(profile)
    if seeded:
        session.add(profile)
        await session.commit()

    pruned = await _prune_invalid_provider_slugs(profile, session)
    if pruned:
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    summary = {
        "queued_slugs": queued,
        "matched_now": 0,
        "seeded_defaults": seeded,
        "pruned_slugs": pruned,
    }
    profile.last_sync_requested_at = datetime.now(UTC)
    profile.last_sync_summary = summary
    if not queued:
        profile.last_sync_completed_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()
    return summary
```

(`SlugFetch` and any-existing-`select` imports stay where they are; just add `from app.models.company import Company` near the other model imports.)

Note: `seed_defaults_if_empty` (called above) currently writes default Greenhouse slugs into `target_company_slugs.greenhouse`. After this change, that helper needs to use the resolver to populate `target_company_ids` instead — see Step 2 below.

- [ ] **Step 2: Update `seed_defaults_if_empty` in `app/services/profile_service.py`**

Locate the function (search via `rg -n 'def seed_defaults_if_empty'`). Today it likely sets:

```python
profile.target_company_slugs = {"greenhouse": [...defaults...]}
```

Change it to no-op for now: the new model is "defaults are seeded by onboarding agent + resolver, not by a backend helper." Replace the function body with:

```python
def seed_defaults_if_empty(profile: UserProfile) -> bool:
    """Default-seeding now happens via the onboarding agent (which calls
    the resolver). This function is retained for callsites but is a no-op."""
    return False
```

(If you want to preserve the seed-on-empty behavior, run the resolver synchronously here for each default name. The spec doesn't require it for V1; keeping `seed_defaults_if_empty` as a no-op is the cleanest path. If existing tests depend on the old behavior, update them to seed via direct DB inserts instead.)

- [ ] **Step 3: Update tests**

Run: `uv run pytest tests/unit/services/ tests/integration/test_application_service.py -v`
Expected: any test that asserts `target_company_slugs.greenhouse` after `prune_and_enqueue`/`sync_profile` will fail. Update them to seed `Company` rows + `target_company_ids` directly instead.

- [ ] **Step 4: Commit**

```bash
git add app/services/job_sync_service.py app/services/profile_service.py tests/
git commit -m "$(cat <<'EOF'
refactor(sync): _prune_invalid_provider_slugs operates on Company.provider_slugs

prune_and_enqueue and sync_profile now read profile.target_company_ids and
walk the followed Company rows. Invalid (provider, slug) pairs are stripped
from Company.provider_slugs; if a Company has no providers left, it's
flagged unfollowable. The user's profile intent is preserved across ATS
migrations.

seed_defaults_if_empty is now a no-op — defaults are owned by the onboarding
agent + resolver path, not a backend helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task D5: Profile API surfaces `target_companies`

**Files:**
- Modify: `app/api/profile.py`
- Test: `tests/unit/api/test_profile.py` (or existing equivalent).

- [ ] **Step 1: Update `GET /api/profile` shape**

In `app/api/profile.py::get_profile`:
- Replace the `"target_company_slugs": profile.target_company_slugs,` line.
- Load Companies whose IDs are in `profile.target_company_ids` and surface them as `target_companies`.

```python
from app.models.company import Company
from sqlmodel import select

# inside get_profile:
companies = []
if profile.target_company_ids:
    rows = (await session.execute(
        select(Company).where(Company.id.in_(profile.target_company_ids))
    )).scalars().all()
    by_id = {c.id: c for c in rows}
    companies = [
        {"id": str(by_id[cid].id), "canonical_name": by_id[cid].canonical_name}
        for cid in profile.target_company_ids
        if cid in by_id
    ]

return {
    ...,
    "target_companies": companies,
    # leave 'target_company_slugs' off the read response — frontend doesn't use it.
}
```

- [ ] **Step 2: Update `PATCH /api/profile` allowed-fields**

Replace `"target_company_slugs"` in the `allowed` set with `"target_company_ids"`. Validate the value is a list of UUID strings; coerce to UUIDs in the service layer or reject malformed payloads with 400.

Concretely, in the `allowed` set:

```python
allowed = {
    "full_name",
    "email",
    "phone",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "target_roles",
    "target_locations",
    "remote_ok",
    "seniority",
    "search_keywords",
    "target_company_ids",  # was target_company_slugs
    "first_name",
    "last_name",
}
```

In `profile_service.update_profile`, ensure UUIDs are parsed if the frontend sends strings:

```python
if "target_company_ids" in data:
    data["target_company_ids"] = [uuid.UUID(x) for x in data["target_company_ids"]]
```

- [ ] **Step 3: Update profile API tests**

Find tests that PATCH `target_company_slugs` and update them to use `target_company_ids` with valid UUIDs (typically pre-seeded `Company` rows).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/api/test_profile.py tests/integration/test_application_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/profile.py app/services/profile_service.py tests/
git commit -m "$(cat <<'EOF'
feat(profile-api): GET surfaces target_companies, PATCH accepts target_company_ids

Read response includes resolved Company objects ({id, canonical_name}) so
the frontend never makes a second round-trip. Patch payload's
target_company_slugs key is removed (target_company_ids replaces it).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track E — Onboarding agent

### Task E1: Rewrite onboarding agent prompts and persistence

**Files:**
- Modify: `app/agents/onboarding.py`
- Test: existing onboarding tests (`tests/unit/agents/test_onboarding.py` if present).

The agent stops thinking in slugs.

- [ ] **Step 1: Rewrite the relevant slugs of the system prompt**

Locate the SYSTEM_PROMPT constant in `app/agents/onboarding.py`. The block around lines 41-50 currently mentions Greenhouse slugs explicitly. Replace it with:

```
4. Learn which **companies** the user wants to follow — REQUIRED. Job sourcing
   is built around the companies the user explicitly tracks. Ask for company
   names (e.g. "Stripe", "Linear", "ByteDance"). Examples to suggest if the
   user is unsure: stripe, anthropic, datadog, figma, notion, vercel, airtable,
   linear. Save them as `target_companies: ["Stripe", "Linear", ...]` (a flat
   list of display names — the backend resolves each to the right ATS
   automatically). Confirm any company that is unfamiliar.
```

The completion gate around lines 75-78 changes:

```
A profile that satisfies only the location gate but has zero followed
companies will produce zero job matches forever — finish the company ask
before wrapping up.
```

(Same semantic — just under the new field name.)

- [ ] **Step 2: Update `save_profile_updates` tool docstring**

Around line 217, change:

```
target_company_slugs (dict, e.g. {"greenhouse": ["stripe", "airbnb"], "lever": [], "ashby": []}),
```

to:

```
target_companies (list of display names, e.g. ["Stripe", "Linear", "ByteDance"] — backend resolves each automatically),
```

- [ ] **Step 3: Rewrite `persist_inferred_slugs` → `persist_inferred_companies`**

Replace the function:

```python
async def persist_inferred_companies(profile, names: list[str], session) -> list[str]:
    """Resolve each company name via the resolver and append to
    profile.target_company_ids. Returns the list of names that resolved.

    Names that fail to resolve are logged and skipped — onboarding does
    not block on them.
    """
    from app.services import company_resolver

    resolved_ids: list[uuid.UUID] = list(profile.target_company_ids or [])
    resolved_names: list[str] = []
    for name in names:
        company = await company_resolver.resolve(name, session)
        if company is None:
            await log.awarning("onboarding.company_unresolved", name=name)
            continue
        if company.id not in resolved_ids:
            resolved_ids.append(company.id)
            resolved_names.append(company.canonical_name)
    profile.target_company_ids = resolved_ids
    session.add(profile)
    await session.commit()
    return resolved_names
```

- [ ] **Step 4: Update `process_tool_results` to consume `target_companies`**

Around lines 303-308, change:

```python
slug_payload = updates.pop("target_company_slugs", None)
```

to:

```python
companies_payload = updates.pop("target_companies", None)
```

…and replace the call site that runs `persist_inferred_slugs` with `persist_inferred_companies(profile, list(companies_payload or []), session)`.

- [ ] **Step 5: Update completion-gate logic and status renderer**

Search for `target_company_slugs` in `app/agents/onboarding.py` and replace each usage:
- Completion gate: `len(profile.target_company_ids) > 0` (was: `target_company_slugs.greenhouse non-empty`).
- Status renderer (around line 138): print resolved canonical names by loading Companies via `select(Company).where(Company.id.in_(profile.target_company_ids))`.

Concretely, around line 129-138:

```python
target_company_ids = data.get("target_company_ids") or []
companies = []
if target_company_ids:
    rows = (await session.execute(
        select(Company).where(Company.id.in_(target_company_ids))
    )).scalars().all()
    companies = [r.canonical_name for r in rows]

return [
    ...,
    f"- target_companies: {_val(companies)}",
]
```

(Add `from app.models.company import Company` at the top of the file.)

- [ ] **Step 6: Run onboarding tests**

Run: `uv run pytest tests/unit/agents/ tests/integration/test_onboarding* -v`
Expected: PASS. Update any failing test that asserts the old `target_company_slugs.greenhouse` path; replace with `target_company_ids` plus a pre-seeded `Company` row.

- [ ] **Step 7: Commit**

```bash
git add app/agents/onboarding.py tests/
git commit -m "$(cat <<'EOF'
feat(onboarding): agent emits target_companies (names), backend routes via resolver

System prompt asks for company display names instead of Greenhouse slugs.
save_profile_updates tool schema replaces target_company_slugs with
target_companies (flat list of strings). persist_inferred_companies routes
each name through company_resolver.resolve and appends Company.id to
profile.target_company_ids. Unresolvable names are logged and skipped — no
silent profile garbage.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track F — Frontend

### Task F1: Update API client types and `resolveCompany` helper

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Update the Profile type**

In `frontend/src/api/client.ts`, locate the Profile type. Change:

```ts
target_company_slugs?: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }
```

to:

```ts
target_companies?: { id: string; canonical_name: string }[]
target_company_ids?: string[]  // optional read-side; PATCH writes this shape
```

- [ ] **Step 2: Add the `resolveCompany` helper**

Append to the `api` object:

```ts
async resolveCompany(name: string): Promise<{ id: string; canonical_name: string; providers: string[] }> {
  const resp = await fetch('/api/companies/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
    credentials: 'include',
  })
  if (resp.status === 404) {
    throw new Error('Couldn\'t find that company on any of our supported boards.')
  }
  if (resp.status === 503) {
    throw new Error('Couldn\'t reach our boards right now, try again.')
  }
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  const body = await resp.json()
  return body.company
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS. Anything red is a stale `target_company_slugs` reference — fix mechanically.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "$(cat <<'EOF'
feat(frontend-api): add resolveCompany, replace target_company_slugs with target_companies

Profile type drops target_company_slugs; reads target_companies (resolved
[{id, canonical_name}]) and writes target_company_ids. resolveCompany maps
the new POST /api/companies/resolve endpoint, surfacing the inline-error
copy for 404/503.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task F2: Replace `TargetSlugsSection` with `FollowedCompaniesSection`

**Files:**
- Create: `frontend/src/components/settings/FollowedCompaniesSection.tsx`
- Create: `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`
- Delete: `frontend/src/components/settings/TargetSlugsSection.tsx`
- Modify: parent that renders the section (likely `frontend/src/pages/Settings.tsx` — search via `rg -n TargetSlugsSection frontend/src`).

- [ ] **Step 1: Write the failing component tests**

Create `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FollowedCompaniesSection } from './FollowedCompaniesSection'
import { api } from '../../api/client'

vi.mock('../../api/client', () => ({
  api: {
    resolveCompany: vi.fn(),
    updateProfile: vi.fn(),
  },
}))

function withQuery(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('FollowedCompaniesSection', () => {
  it('shows existing companies as chips', () => {
    render(withQuery(
      <FollowedCompaniesSection companies={[{ id: 'a', canonical_name: 'Linear' }]} />
    ))
    expect(screen.getByText('Linear')).toBeInTheDocument()
  })

  it('resolves a typed company on Enter and adds a chip', async () => {
    ;(api.resolveCompany as any).mockResolvedValue({
      id: 'b',
      canonical_name: 'Stripe',
      providers: ['greenhouse'],
    })
    ;(api.updateProfile as any).mockResolvedValue({ id: 'p', updated: true })

    render(withQuery(<FollowedCompaniesSection companies={[]} />))

    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'Stripe{Enter}')

    await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument())
    expect(api.resolveCompany).toHaveBeenCalledWith('Stripe')
    expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: ['b'] })
  })

  it('shows inline error on 404', async () => {
    ;(api.resolveCompany as any).mockRejectedValue(
      new Error("Couldn't find that company on any of our supported boards.")
    )

    render(withQuery(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'nope-co{Enter}')

    expect(await screen.findByText(/Couldn't find that company/i)).toBeInTheDocument()
  })

  it('rolls back chip on PATCH failure', async () => {
    ;(api.resolveCompany as any).mockResolvedValue({
      id: 'c',
      canonical_name: 'Linear',
      providers: ['ashby'],
    })
    ;(api.updateProfile as any).mockRejectedValue(new Error('boom'))

    render(withQuery(<FollowedCompaniesSection companies={[]} />))
    await userEvent.type(screen.getByPlaceholderText(/Add a company/i), 'Linear{Enter}')

    // Chip rolled back; toast/error visible.
    await waitFor(() => expect(screen.queryByText('Linear')).not.toBeInTheDocument())
  })

  it('removes a chip and PATCHes without that id', async () => {
    ;(api.updateProfile as any).mockResolvedValue({ id: 'p', updated: true })
    render(withQuery(<FollowedCompaniesSection companies={[
      { id: 'a', canonical_name: 'Linear' },
      { id: 'b', canonical_name: 'Stripe' },
    ]} />))

    fireEvent.click(screen.getByLabelText(/Remove Linear/i))
    expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: ['b'] })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- FollowedCompaniesSection`
Expected: FAIL — component doesn't exist.

- [ ] **Step 3: Implement `frontend/src/components/settings/FollowedCompaniesSection.tsx`**

```tsx
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface Company {
  id: string
  canonical_name: string
}

export interface FollowedCompaniesSectionProps {
  companies: Company[]
}

export function FollowedCompaniesSection({ companies }: FollowedCompaniesSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [optimistic, setOptimistic] = useState<Company[]>(companies)
  const [busy, setBusy] = useState(false)

  // Keep optimistic state in sync if parent prop changes.
  // (Simple approach; for a real app a useEffect on companies would also work.)
  if (companies !== optimistic && !busy) {
    setOptimistic(companies)
  }

  const patch = useMutation({
    mutationFn: (ids: string[]) => api.updateProfile({ target_company_ids: ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
  })

  async function add() {
    const name = draft.trim()
    if (!name) return
    setError(null)
    setBusy(true)
    let resolved: { id: string; canonical_name: string } | null = null
    try {
      resolved = await api.resolveCompany(name)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
      return
    }
    const next = [...optimistic, { id: resolved.id, canonical_name: resolved.canonical_name }]
    setOptimistic(next)
    setDraft('')
    track('settings.company_added', { company_id: resolved.id, canonical_name: resolved.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)  // rollback
      show((e as Error)?.message ?? 'Could not save', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    const company = optimistic.find(c => c.id === id)
    const next = optimistic.filter(c => c.id !== id)
    setOptimistic(next)
    track('settings.company_removed', { company_id: id, canonical_name: company?.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)  // rollback
      show((e as Error)?.message ?? 'Could not save', 'error')
    }
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Followed companies</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-3">
        <p className="text-sm text-subtle">We'll match you to roles posted by these companies.</p>
        <div className="flex flex-wrap gap-2">
          {optimistic.map(c => (
            <span
              key={c.id}
              className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text"
            >
              {c.canonical_name}
              <button
                type="button"
                aria-label={`Remove ${c.canonical_name}`}
                onClick={() => remove(c.id)}
                className="text-muted hover:text-danger"
              >×</button>
            </span>
          ))}
          {optimistic.length === 0 && (
            <p className="text-xs text-subtle">No companies followed yet.</p>
          )}
        </div>
        <div>
          <input
            type="text"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
            placeholder="Add a company you want to follow"
            disabled={busy}
            className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
          />
          {error && (
            <p role="alert" className="text-xs text-danger mt-1">{error}</p>
          )}
        </div>
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run component tests**

Run: `cd frontend && npm test -- FollowedCompaniesSection`
Expected: PASS.

- [ ] **Step 5: Wire into the parent settings page**

Search: `rg -n TargetSlugsSection frontend/src`
Modify each file to import `FollowedCompaniesSection` and pass `profile.target_companies` instead of `profile.target_company_slugs`. Example diff in `frontend/src/pages/Settings.tsx` (or wherever):

```diff
- import { TargetSlugsSection } from '../components/settings/TargetSlugsSection'
+ import { FollowedCompaniesSection } from '../components/settings/FollowedCompaniesSection'
...
- <TargetSlugsSection slugs={profile.target_company_slugs ?? {}} />
+ <FollowedCompaniesSection companies={profile.target_companies ?? []} />
```

- [ ] **Step 6: Delete the old component**

```bash
git rm frontend/src/components/settings/TargetSlugsSection.tsx
# delete its test file too if it exists:
git rm -f frontend/src/components/settings/TargetSlugsSection.test.tsx
```

- [ ] **Step 7: Run the full frontend test suite + typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS. Fix any stale `TargetSlugsSection` references mechanically.

- [ ] **Step 8: Run dev server and verify visually**

Run: `cd frontend && npm run dev`
Open `http://localhost:5173`, log in, navigate to Settings, exercise: add a real company by name, expect a chip; add a junk string, expect an inline error; remove a chip, expect it to disappear and re-PATCH. Capture screenshots — they're attached to the PR per the user-memory `feedback_frontend_pr_screenshots.md`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(frontend): FollowedCompaniesSection replaces three-tab TargetSlugsSection

Single text input, debounced submit, optimistic chip + rollback on PATCH
failure, inline error copy for 404/503. Provider concept gone from the UI.
target_company_slugs API shape removed in favor of target_companies (read)
and target_company_ids (write).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track G — Integration and smoke tests

### Task G1: Full resolution-flow integration test

**Files:**
- Create: `tests/integration/test_company_resolution_flow.py`

End-to-end test: HTTP POST → resolver → fan-out (mocked at the httpx layer) → Company persistence → profile PATCH → GET surfaces under target_companies.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_company_resolution_flow.py`:

```python
"""End-to-end resolution flow with the httpx layer mocked."""

import pytest
import respx
from httpx import Response


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_then_get_profile_surfaces_company(client, auth_profile):
    # Mock all three providers: greenhouse 200, lever 404, ashby 200.
    respx.get("https://boards-api.greenhouse.io/v1/boards/linear").respond(200)
    respx.get("https://api.lever.co/v0/postings/linear").respond(404)
    respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").respond(200, json={"jobs": []})

    resp = await client.post("/api/companies/resolve", json={"name": "Linear"})
    assert resp.status_code == 200
    company_id = resp.json()["company"]["id"]

    # PATCH profile with the new id
    profile_resp = await client.get("/api/profile")
    current_ids = [c["id"] for c in profile_resp.json().get("target_companies", [])]
    new_ids = current_ids + [company_id]
    patch_resp = await client.patch("/api/profile", json={"target_company_ids": new_ids})
    assert patch_resp.status_code == 200

    # GET surfaces it
    profile_after = await client.get("/api/profile")
    names = [c["canonical_name"] for c in profile_after.json()["target_companies"]]
    assert "Linear" in names


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_no_match_returns_404(client, auth_profile):
    respx.get("https://boards-api.greenhouse.io/v1/boards/nope-co").respond(404)
    respx.get("https://api.lever.co/v0/postings/nope-co").respond(404)
    respx.get("https://api.ashbyhq.com/posting-api/job-board/nope-co").respond(404)

    resp = await client.post("/api/companies/resolve", json={"name": "nope-co"})
    assert resp.status_code == 404


@respx.mock
@pytest.mark.asyncio
async def test_post_resolve_multi_provider_match_persists_all(client, auth_profile):
    respx.get("https://boards-api.greenhouse.io/v1/boards/migrating-co").respond(200)
    respx.get("https://api.lever.co/v0/postings/migrating-co").respond(404)
    respx.get("https://api.ashbyhq.com/posting-api/job-board/migrating-co").respond(200, json={"jobs": []})

    resp = await client.post("/api/companies/resolve", json={"name": "migrating-co"})
    assert resp.status_code == 200
    assert set(resp.json()["company"]["providers"]) == {"greenhouse", "ashby"}
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_company_resolution_flow.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_company_resolution_flow.py
git commit -m "$(cat <<'EOF'
test(integration): full company resolution flow with mocked ATS HTTP

POST /api/companies/resolve -> resolver -> fan-out (httpx-mocked) -> Company
persistence -> profile PATCH -> GET surfaces under target_companies. Covers
single-provider match, multi-provider match, and full miss.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task G2: Smoke test against the live server

**Files:**
- Create: `tests/smoke/test_company_resolution.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/smoke/test_company_resolution.py`:

```python
"""Smoke test: resolve 'Stripe' against the real Greenhouse public API.

Requires the local dev server running on :8000 with --has-seed-api enabled.
The adapter HTTP layer is the only piece NOT mocked; we accept the test
depending on Greenhouse's public board being up.
"""

import pytest


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_resolve_stripe_real_greenhouse(smoke_client, smoke_seed_profile):
    resp = await smoke_client.post("/api/companies/resolve", json={"name": "Stripe"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["canonical_name"].lower() == "stripe"
    assert "greenhouse" in body["company"]["providers"]

    company_id = body["company"]["id"]
    patch = await smoke_client.patch("/api/profile", json={"target_company_ids": [company_id]})
    assert patch.status_code == 200

    profile = await smoke_client.get("/api/profile")
    names = [c["canonical_name"].lower() for c in profile.json()["target_companies"]]
    assert "stripe" in names
```

(`smoke_client` and `smoke_seed_profile` are existing smoke-test fixtures — follow the pattern from any other file under `tests/smoke/`.)

- [ ] **Step 2: Run the smoke test**

Run: `uv run pytest tests/smoke/test_company_resolution.py --has-seed-api -v`
(Requires `uv run uvicorn app.main:app --reload --port 8000` to be running.)
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_company_resolution.py
git commit -m "$(cat <<'EOF'
test(smoke): resolve 'Stripe' against the live Greenhouse public API

Single end-to-end test that exercises the resolver, the resolve endpoint,
the PATCH path, and GET /api/profile against a real Greenhouse board with
no HTTP mocking. Adapters for Lever/Ashby remain unit/integration-tested
only — the real ATS APIs aren't exercised in CI to avoid flake.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track H — Final verification

### Task H1: Run the full suite + frontend e2e

- [ ] **Step 1: Run full backend suite**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 2: Run frontend tests + typecheck + e2e**

Run: `cd frontend && npm test && npx tsc --noEmit && npx playwright test`
Expected: PASS. (E2E may require dev server + backend running; follow the project's existing e2e runbook.)

- [ ] **Step 3: Verify migration applies cleanly on a fresh DB**

Run: `docker compose down -v && docker compose up -d db && make migrate ARGS="upgrade head"`
Expected: migration completes; no errors.

- [ ] **Step 4: Manually exercise the UI**

Run: `uv run uvicorn app.main:app --reload --port 8000` and `cd frontend && npm run dev`. Log in, navigate to Settings, follow a company, verify the chip persists across page reload.

- [ ] **Step 5: Capture screenshots for the PR**

Take before/after screenshots of the Settings → Followed companies section. The frontend-PR-screenshots memory is non-negotiable.

- [ ] **Step 6: Final clean-commit pass**

Run: `git log --oneline main..HEAD` to inspect the full set of commits.
Run: `git status` — should be clean.

The PR body should reference: spec at `docs/superpowers/specs/2026-05-08-provider-agnostic-companies-design.md`, the dropped Lever/Ashby slug data note, and the deferred follow-up that drops `user_profiles.target_company_slugs` one release on.

---

## Self-review notes

- All description-rename references checked: `Job.description_md`/`description_clean` in models, services, JobData; corresponding test fixtures.
- Provider name canonicalization: migration UPDATEs jobs/slug_fetches; adapter `provider_name` flips in B3; SOURCES key matches.
- Greenhouse double-clean fix: `_html_to_markdown` removed; `description_raw` stores raw HTML; cleaner runs once in `job_service.upsert_job`.
- Onboarding agent: prompt, tool schema, persistence helper, completion gate, status renderer all updated.
- Frontend: types, component, parent wiring, deletions, screenshots.
- Migration safety: spec's "deferred-deploy hazard" section is honored — code change + migration ship together.
- Multi-match policy: resolver persists every confirming provider; scheduler walks `Company.provider_slugs`; same role on two providers becomes two `Job` rows pointing at one `Company`.

## Out-of-scope

- **Drop `user_profiles.target_company_slugs`** — separate Alembic revision in a follow-up PR, one release on.
- Layer 2 (curated catalog + typeahead).
- Layer 3 (chat-driven semantic matching).
- Real-API tests for Lever/Ashby (avoid flake; only Greenhouse is smoke-tested).
