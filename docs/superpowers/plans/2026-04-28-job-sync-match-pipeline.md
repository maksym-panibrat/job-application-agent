# Job Sync & Match Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synchronous `POST /api/jobs/sync` (which blew past Cloud Run's 300s timeout) with a 202-returning, queue-driven pipeline that fetches each Greenhouse slug at most once per 6h, matches strictly within a user's slug list, auto-prunes invalid slugs, and seeds 5 defaults to brand-new users.

**Architecture:** Two DB-backed queues (`slug_fetches` for slug-level fetch state; `applications.match_status` for per-(profile, job) match work) drained by 15-min cron workers with a 240s budget per tick. "Sync now" returns 202 in <1s, scoring against the cached pool and enqueuing stale slugs for background catch-up. Spec: `docs/superpowers/specs/2026-04-28-job-sync-match-pipeline-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy async, asyncpg, Alembic, httpx, respx (test mock), pytest + pytest-asyncio, testcontainers Postgres, React/Vite frontend.

---

## File Structure

**New files**
- `app/models/slug_fetch.py` — `SlugFetch` SQLModel (per-`(source, slug)` fetch state, lease columns, invalid flag)
- `app/services/slug_registry_service.py` — owns `slug_fetches`: validate, mark_fetched, enqueue_stale, next_pending
- `app/services/match_queue_service.py` — owns the `pending_match` queue: enqueue_for_interested_profiles, next_batch, mark_done/error
- `app/data/__init__.py` — empty package marker
- `app/data/default_slugs.py` — `DEFAULT_SLUGS: list[str]` (15 hand-picked Greenhouse-active companies)
- `app/data/slug_company.py` — slug ↔ company_name mapping helpers (forward + reverse) + regression tests
- `alembic/versions/<rev>_add_slug_fetches_and_match_queue.py` — schema + backfill migration
- `tests/unit/test_slug_registry_service.py`
- `tests/unit/test_match_queue_service.py`
- `tests/unit/test_default_slugs_catalog.py`
- `tests/unit/test_slug_company_mapping.py`
- `tests/integration/test_slug_registry.py`
- `tests/integration/test_match_queue.py`
- `tests/integration/test_sync_queue_cron.py`
- `tests/integration/test_match_queue_cron.py`
- `tests/integration/test_sync_status_endpoint.py`
- `frontend/src/components/SyncStatusChip.tsx`
- `frontend/src/components/InvalidSlugsNotice.tsx`

**Modified files**
- `app/models/__init__.py` — register `SlugFetch`
- `app/models/application.py` — add `match_status`, `match_attempts`, `match_queued_at`, `match_claimed_at` columns
- `app/models/user_profile.py` — add `last_sync_requested_at`, `last_sync_completed_at`, `last_sync_summary`
- `app/config.py` — `job_stale_after_days` default 14 → 21
- `app/sources/greenhouse_board.py` — shared `httpx.AsyncClient`, tighter `Timeout`, `validate()`, `fetch_jobs(slug, since=...)`
- `app/services/job_sync_service.py` — rewrite `sync_profile` to enqueue-only + score-cached
- `app/services/match_service.py` — add `score_cached`; change `score_and_match` candidate query to filter by profile slugs
- `app/services/job_service.py` — `mark_stale_jobs` already takes `stale_after_days` param, no change
- `app/services/profile_service.py` — `seed_defaults_if_empty(profile)` helper
- `app/api/jobs.py` — `/sync` returns 202; remove `BackgroundTasks` and `_score_after_sync`
- `app/api/internal_cron.py` — add `process_sync_queue` and `process_match_queue` endpoints
- `app/scheduler/tasks.py` — add `run_sync_queue`, `run_match_queue`; rewrite `run_job_sync` to bulk-enqueue
- `app/agents/onboarding.py` — validate every slug before persisting
- `.github/workflows/cron.yml` — add 15-min queue-drain schedules
- `frontend/src/pages/Dashboard.tsx` (or equivalent) — wire toast, status chip, invalid-slugs notice

---

## PHASE 1 — Foundation

Independently shippable. No API contract changes. Adds tables and the slug registry service used by later phases.

### Task 1: Migration — schema for slug_fetches, match queue, sync visibility

**Files:**
- Create: `alembic/versions/<rev>_add_slug_fetches_and_match_queue.py`
- Test: `tests/integration/test_slug_registry.py` (smoke that the table exists)

- [ ] **Step 1: Generate the migration scaffold**

```bash
make migrate ARGS='revision -m "add slug_fetches and match queue"'
```

Expected: a new file appears in `alembic/versions/`. Note the revision id (e.g. `7e4a1c9b2f10`).

- [ ] **Step 2: Replace the migration body with the full schema**

Open the new file. Replace `upgrade()` and `downgrade()` with:

```python
def upgrade() -> None:
    # New table: slug_fetches
    op.create_table(
        "slug_fetches",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("consecutive_404_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_5xx_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_invalid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("invalid_reason", sa.String(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("source", "slug", name="pk_slug_fetches"),
    )
    op.create_index(
        "ix_slug_fetches_queued",
        "slug_fetches",
        ["queued_at", "claimed_at"],
    )

    # Application: match queue columns
    op.add_column("applications", sa.Column("match_status", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("match_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("applications", sa.Column("match_queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("match_claimed_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill: any application that already has a score is matched; rest are pending_match
    op.execute(
        "UPDATE applications SET match_status = "
        "CASE WHEN match_score IS NOT NULL THEN 'matched' ELSE 'pending_match' END"
    )
    op.alter_column("applications", "match_status", nullable=False, server_default="pending_match")
    op.create_index(
        "ix_applications_match_queue",
        "applications",
        ["match_status", "match_queued_at"],
    )

    # UserProfile: sync visibility
    op.add_column("user_profiles", sa.Column("last_sync_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_profiles", sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "user_profiles",
        sa.Column(
            "last_sync_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Backfill slug_fetches: every distinct slug across active profiles seeded NULL last_fetched_at
    # so the next cron treats them all as new (fetch immediately).
    op.execute(
        """
        INSERT INTO slug_fetches (source, slug)
        SELECT DISTINCT 'greenhouse_board', jsonb_array_elements_text(
            COALESCE(target_company_slugs->'greenhouse', '[]'::jsonb)
        )
        FROM user_profiles
        WHERE search_active = true
        ON CONFLICT (source, slug) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_applications_match_queue", table_name="applications")
    op.drop_column("applications", "match_claimed_at")
    op.drop_column("applications", "match_queued_at")
    op.drop_column("applications", "match_attempts")
    op.drop_column("applications", "match_status")
    op.drop_column("user_profiles", "last_sync_summary")
    op.drop_column("user_profiles", "last_sync_completed_at")
    op.drop_column("user_profiles", "last_sync_requested_at")
    op.drop_index("ix_slug_fetches_queued", table_name="slug_fetches")
    op.drop_table("slug_fetches")
```

Add the postgresql import at the top of the migration file:

```python
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 3: Apply against the local test container**

```bash
docker compose up -d db
make migrate ARGS='upgrade head'
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade … -> 7e4a1c9b2f10, add slug_fetches and match queue`.

- [ ] **Step 4: Verify the schema with psql**

```bash
docker compose exec db psql -U postgres -d postgres -c '\d slug_fetches' -c '\d applications' -c '\d user_profiles'
```

Expected: `slug_fetches` table exists with all columns; `applications` shows `match_status`, `match_attempts`, `match_queued_at`, `match_claimed_at`; `user_profiles` shows `last_sync_requested_at` etc.

- [ ] **Step 5: Test downgrade then upgrade once to confirm reversibility**

```bash
make migrate ARGS='downgrade -1'
make migrate ARGS='upgrade head'
```

Expected: both succeed without error.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): add slug_fetches table and match queue columns"
```

---

### Task 2: SlugFetch SQLModel + register

**Files:**
- Create: `app/models/slug_fetch.py`
- Modify: `app/models/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_slug_registry.py`:

```python
"""Integration tests for slug_fetches model + slug_registry_service."""
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.models.slug_fetch import SlugFetch


@pytest.mark.asyncio
async def test_slug_fetch_round_trip(db_session):
    row = SlugFetch(source="greenhouse_board", slug="airbnb")
    db_session.add(row)
    await db_session.commit()

    result = await db_session.execute(
        select(SlugFetch).where(
            SlugFetch.source == "greenhouse_board",
            SlugFetch.slug == "airbnb",
        )
    )
    fetched = result.scalar_one()
    assert fetched.is_invalid is False
    assert fetched.consecutive_404_count == 0
    assert fetched.last_fetched_at is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_slug_registry.py::test_slug_fetch_round_trip -v
```

Expected: FAIL with `ModuleNotFoundError: app.models.slug_fetch`.

- [ ] **Step 3: Create the model**

`app/models/slug_fetch.py`:

```python
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class SlugFetch(SQLModel, table=True):
    __tablename__ = "slug_fetches"
    source: str = Field(primary_key=True)
    slug: str = Field(primary_key=True)
    last_fetched_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_attempted_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_status: str | None = None
    consecutive_404_count: int = 0
    consecutive_5xx_count: int = 0
    is_invalid: bool = False
    invalid_reason: str | None = None
    queued_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    claimed_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
```

- [ ] **Step 4: Register in models package**

Edit `app/models/__init__.py` to add the import (look at existing imports for the pattern; add `from app.models.slug_fetch import SlugFetch  # noqa: F401`).

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_slug_registry.py::test_slug_fetch_round_trip -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models/slug_fetch.py app/models/__init__.py tests/integration/test_slug_registry.py
git commit -m "feat(models): add SlugFetch model"
```

---

### Task 3: Settings — bump job_stale_after_days to 21

**Files:**
- Modify: `app/config.py:27`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_job_stale_after_days_default_is_21():
    """Stale TTL bumped from 14d to 21d (spec 2026-04-28)."""
    from app.config import Settings
    s = Settings(database_url="postgresql://x:x@x/x")
    assert s.job_stale_after_days == 21
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_config.py::test_job_stale_after_days_default_is_21 -v
```

Expected: FAIL — current default is 14.

- [ ] **Step 3: Change the default**

In `app/config.py`, change `job_stale_after_days: int = 14` to `job_stale_after_days: int = 21`.

- [ ] **Step 4: Run test + the existing job-staleness test to verify nothing else broke**

```bash
uv run pytest tests/unit/test_config.py tests/integration/test_job_sync.py -v
```

Expected: all PASS. The existing `test_mark_stale_jobs` passes `stale_after_days=14` explicitly so it's not affected.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/unit/test_config.py
git commit -m "feat(config): bump job staleness TTL from 14d to 21d"
```

---

### Task 4: slug_company mapping (forward + reverse)

**Files:**
- Create: `app/data/__init__.py`, `app/data/slug_company.py`
- Test: `tests/unit/test_slug_company_mapping.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_slug_company_mapping.py`:

```python
"""slug ↔ company_name round-trip + edge cases.

This mapping is the brittle bridge between the global Job table (keyed by
company_name) and the per-profile slug list. Locking it down with tests so
a refactor to a Company table later is safe."""

import pytest

from app.data.slug_company import slug_to_company_name, company_name_to_slug


@pytest.mark.parametrize("slug,expected", [
    ("airbnb", "Airbnb"),
    ("stripe", "Stripe"),
    ("dropbox-engineering", "Dropbox Engineering"),
    ("a-b-c", "A B C"),
])
def test_slug_to_company_name(slug, expected):
    assert slug_to_company_name(slug) == expected


@pytest.mark.parametrize("name,expected", [
    ("Airbnb", "airbnb"),
    ("Dropbox Engineering", "dropbox-engineering"),
    ("Notion Labs", "notion-labs"),
])
def test_company_name_to_slug(name, expected):
    assert company_name_to_slug(name) == expected


@pytest.mark.parametrize("slug", ["airbnb", "stripe", "dropbox-engineering"])
def test_round_trip(slug):
    """company_name_to_slug(slug_to_company_name(slug)) must equal slug."""
    assert company_name_to_slug(slug_to_company_name(slug)) == slug
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_slug_company_mapping.py -v
```

Expected: FAIL with `ModuleNotFoundError: app.data.slug_company`.

- [ ] **Step 3: Create the package + module**

`app/data/__init__.py`:

```python
```

(empty file)

`app/data/slug_company.py`:

```python
"""Bidirectional slug ↔ company_name mapping.

The forward transform `slug.replace("-", " ").title()` was already used by
`GreenhouseBoardSource._parse_job` to set Job.company_name. We centralize it
here and add the reverse so the match queue can find profiles interested in a
job by reverse-mapping its company_name back to a slug."""


def slug_to_company_name(slug: str) -> str:
    return slug.replace("-", " ").title()


def company_name_to_slug(name: str) -> str:
    return name.lower().replace(" ", "-")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_slug_company_mapping.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data/ tests/unit/test_slug_company_mapping.py
git commit -m "feat(data): centralize slug↔company_name mapping"
```

---

### Task 5: GreenhouseBoardSource — shared client, tighter timeouts, `validate()`

**Files:**
- Modify: `app/sources/greenhouse_board.py`
- Test: `tests/unit/test_greenhouse_board_source.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_greenhouse_board_source.py`:

```python
@pytest.mark.asyncio
async def test_validate_slug_returns_true_on_200():
    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb").mock(
            return_value=httpx.Response(200, json={"name": "Airbnb", "content": "<p/>"})
        )
        assert await source.validate("airbnb") is True


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404():
    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        assert await source.validate("openai") is False


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_5xx():
    """Transient errors should fail-closed for validate (don't add a slug we can't verify)."""
    source = GreenhouseBoardSource()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/flaky").mock(
            return_value=httpx.Response(503)
        )
        assert await source.validate("flaky") is False


@pytest.mark.asyncio
async def test_uses_shared_client_when_provided():
    """When called with a shared client, no per-call client is created."""
    source = GreenhouseBoardSource()
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(f"{GREENHOUSE_BOARDS_BASE}/stripe/jobs").mock(
                return_value=httpx.Response(200, json=STRIPE_JOB_FIXTURE)
            )
            jobs, _ = await source.search("", None, slug="stripe", client=client)
    assert len(jobs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_greenhouse_board_source.py -v -k "validate or shared_client"
```

Expected: FAIL with `AttributeError: 'GreenhouseBoardSource' object has no attribute 'validate'` (and similar for `client=` kwarg).

- [ ] **Step 3: Implement validate() and shared-client support**

Replace `app/sources/greenhouse_board.py` with:

```python
"""Greenhouse board job source adapter."""
from datetime import datetime
from typing import Any

import httpx
import markdownify
import structlog

from app.sources.base import JobData, JobSource

GREENHOUSE_BOARDS_BASE = "https://boards-api.greenhouse.io/v1/boards"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

log = structlog.get_logger()


def _html_to_markdown(content: str | None) -> str | None:
    if not content:
        return content
    return markdownify.markdownify(content, strip=["script", "style"]).strip() or None


class GreenhouseFetchError(Exception):
    def __init__(self, slug: str, message: str = ""):
        self.slug = slug
        super().__init__(message or slug)


class InvalidSlugError(GreenhouseFetchError):
    """404 — board doesn't exist."""


class TransientFetchError(GreenhouseFetchError):
    """5xx or network error — retry next cycle."""


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
        from app.data.slug_company import slug_to_company_name
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
            description_md=_html_to_markdown(item.get("content")),
            salary=None,
            contract_type=None,
            apply_url=apply_url,
            posted_at=posted_at,
        )

    async def validate(
        self, slug: str, *, client: httpx.AsyncClient | None = None
    ) -> bool:
        """Cheap existence check via GET /v1/boards/{slug}. True iff 200."""
        url = f"{GREENHOUSE_BOARDS_BASE}/{slug}"
        try:
            if client is not None:
                resp = await client.get(url, timeout=DEFAULT_TIMEOUT)
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
                response = await client.get(url, params=params, timeout=DEFAULT_TIMEOUT)
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
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[list[JobData], None]:
        if slug is None:
            return [], None
        jobs = await self._fetch_slug(slug, client=client)
        return jobs, None

    async def fetch_jobs(
        self,
        slug: str,
        *,
        since: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[JobData]:
        """Fetch all jobs for a slug, optionally filtering by `posted_at >= since`.

        Greenhouse public API has no server-side date filter, so the filter is
        applied client-side after the full payload is parsed."""
        jobs = await self._fetch_slug(slug, client=client)
        if since is None:
            return jobs
        return [j for j in jobs if j.posted_at is None or j.posted_at >= since]
```

- [ ] **Step 4: Run all source tests to verify**

```bash
uv run pytest tests/unit/test_greenhouse_board_source.py -v
```

Expected: all PASS, including pre-existing tests (signature backward compatible — `client` is keyword-only).

- [ ] **Step 5: Commit**

```bash
git add app/sources/greenhouse_board.py tests/unit/test_greenhouse_board_source.py
git commit -m "feat(sources): add validate() + fetch_jobs(since) + shared httpx client"
```

---

### Task 6: slug_registry_service — validate, mark_fetched

**Files:**
- Create: `app/services/slug_registry_service.py`
- Test: `tests/integration/test_slug_registry.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_slug_registry.py`:

```python
import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


@pytest.mark.asyncio
async def test_validate_slug_writes_row_on_success(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb").mock(
            return_value=httpx.Response(200, json={"name": "Airbnb", "content": "<p/>"})
        )
        ok = await slug_registry_service.validate_slug(
            "greenhouse_board", "airbnb", db_session
        )
    assert ok is True
    row = await slug_registry_service.get(
        "greenhouse_board", "airbnb", db_session
    )
    assert row is not None
    assert row.last_status == "ok"
    assert row.last_fetched_at is None  # validate is existence-only


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404_and_writes_no_row(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai").mock(
            return_value=httpx.Response(404)
        )
        ok = await slug_registry_service.validate_slug(
            "greenhouse_board", "openai", db_session
        )
    assert ok is False
    row = await slug_registry_service.get(
        "greenhouse_board", "openai", db_session
    )
    assert row is None


@pytest.mark.asyncio
async def test_mark_fetched_ok_resets_counters(db_session):
    await slug_registry_service.mark_fetched(
        "greenhouse_board", "stripe", "ok", db_session
    )
    row = await slug_registry_service.get("greenhouse_board", "stripe", db_session)
    assert row.last_status == "ok"
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 0
    assert row.last_fetched_at is not None
    assert row.queued_at is None
    assert row.claimed_at is None


@pytest.mark.asyncio
async def test_mark_fetched_invalid_increments_404_and_flips_at_2(db_session):
    await slug_registry_service.mark_fetched(
        "greenhouse_board", "openai", "invalid", db_session
    )
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False  # one strike

    await slug_registry_service.mark_fetched(
        "greenhouse_board", "openai", "invalid", db_session
    )
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True  # two strikes — pruned
    assert row.invalid_reason is not None


@pytest.mark.asyncio
async def test_mark_fetched_transient_does_not_count_toward_invalid(db_session):
    for _ in range(5):
        await slug_registry_service.mark_fetched(
            "greenhouse_board", "flaky", "transient_error", db_session
        )
    row = await slug_registry_service.get("greenhouse_board", "flaky", db_session)
    assert row.is_invalid is False
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_slug_registry.py -v
```

Expected: FAIL with `ModuleNotFoundError: app.services.slug_registry_service`.

- [ ] **Step 3: Implement the service**

Create `app/services/slug_registry_service.py`:

```python
"""Slug-level fetch state. One row per (source, slug), shared across users.

Lifecycle:
  validate_slug   → writes row with last_status='ok' (no fetch yet)
  enqueue_stale   → sets queued_at on existing row (or inserts then sets)
  next_pending    → claims rows by setting claimed_at
  mark_fetched    → updates last_status, counters, clears queued_at + claimed_at,
                    flips is_invalid after 2 consecutive 404s
"""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select

from app.models.slug_fetch import SlugFetch
from app.sources.greenhouse_board import GreenhouseBoardSource

INVALID_THRESHOLD = 2
log = structlog.get_logger()


async def get(source: str, slug: str, session: AsyncSession) -> SlugFetch | None:
    result = await session.execute(
        select(SlugFetch).where(SlugFetch.source == source, SlugFetch.slug == slug)
    )
    return result.scalar_one_or_none()


async def validate_slug(source: str, slug: str, session: AsyncSession) -> bool:
    """Returns True if the slug exists on Greenhouse. On True, upserts a row
    with last_status='ok' (no last_fetched_at — that's set by an actual fetch)."""
    if source != "greenhouse_board":
        raise ValueError(f"validate_slug only supports greenhouse_board (got {source})")
    src = GreenhouseBoardSource()
    ok = await src.validate(slug)
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


async def mark_fetched(
    source: str, slug: str, status: str, session: AsyncSession, *, error: str | None = None
) -> SlugFetch:
    """Record a fetch outcome. status ∈ {'ok','invalid','transient_error'}."""
    now = datetime.now(UTC)
    row = await get(source, slug, session)
    if row is None:
        row = SlugFetch(source=source, slug=slug)
        session.add(row)

    row.last_attempted_at = now
    row.last_status = status
    row.queued_at = None
    row.claimed_at = None

    if status == "ok":
        row.last_fetched_at = now
        row.consecutive_404_count = 0
        row.consecutive_5xx_count = 0
    elif status == "invalid":
        row.consecutive_404_count += 1
        row.consecutive_5xx_count = 0
        if row.consecutive_404_count >= INVALID_THRESHOLD:
            row.is_invalid = True
            row.invalid_reason = error or "Greenhouse returned 404 (board not found)"
            await log.awarning(
                "slug_registry.invalidated",
                source=source, slug=slug, count=row.consecutive_404_count,
            )
    elif status == "transient_error":
        row.consecutive_5xx_count += 1
    else:
        raise ValueError(f"unknown status: {status}")

    await session.commit()
    await session.refresh(row)
    return row


async def enqueue_stale(
    profile, session: AsyncSession, *, ttl_hours: int = 6
) -> list[str]:
    """For each greenhouse slug on the profile that's not invalid:
    if its last_fetched_at is NULL or older than now-ttl_hours, set queued_at=now().
    Returns the list of slugs newly queued (excluding ones already queued)."""
    slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
    if not slugs:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    queued: list[str] = []
    for slug in slugs:
        row = await get("greenhouse_board", slug, session)
        if row is None:
            row = SlugFetch(source="greenhouse_board", slug=slug, queued_at=datetime.now(UTC))
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


async def next_pending(
    session: AsyncSession, *, limit: int, lease_seconds: int = 300
) -> list[SlugFetch]:
    """Claim up to `limit` queued rows. A row is claimable if queued_at is set
    and (claimed_at is NULL or older than lease_seconds ago).
    Selected rows have claimed_at set to now() before return."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    result = await session.execute(
        select(SlugFetch)
        .where(
            SlugFetch.queued_at.is_not(None),
            (SlugFetch.claimed_at.is_(None)) | (SlugFetch.claimed_at < cutoff),
            SlugFetch.is_invalid.is_(False),
        )
        .order_by(SlugFetch.queued_at)
        .limit(limit)
    )
    rows = list(result.scalars().all())
    now = datetime.now(UTC)
    for row in rows:
        row.claimed_at = now
    if rows:
        await session.commit()
    return rows


async def pending_count(session: AsyncSession) -> int:
    from sqlalchemy import func
    result = await session.execute(
        select(func.count()).select_from(SlugFetch).where(
            SlugFetch.queued_at.is_not(None),
            SlugFetch.is_invalid.is_(False),
        )
    )
    return int(result.scalar_one())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_slug_registry.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/slug_registry_service.py tests/integration/test_slug_registry.py
git commit -m "feat(services): slug_registry_service (validate, mark, enqueue, claim)"
```

---

### Task 7: slug_registry_service — enqueue_stale + next_pending edge cases

**Files:**
- Test: `tests/integration/test_slug_registry.py` (extend)

- [ ] **Step 1: Write tests for enqueue/lease semantics**

Append to `tests/integration/test_slug_registry.py`:

```python
from datetime import timedelta

from app.models.user_profile import UserProfile


def _profile_with_slugs(*slugs: str) -> UserProfile:
    import uuid
    return UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": list(slugs)},
    )


@pytest.mark.asyncio
async def test_enqueue_stale_inserts_for_unknown_slugs(db_session):
    profile = _profile_with_slugs("airbnb", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert sorted(queued) == ["airbnb", "stripe"]
    for slug in ["airbnb", "stripe"]:
        row = await slug_registry_service.get("greenhouse_board", slug, db_session)
        assert row.queued_at is not None


@pytest.mark.asyncio
async def test_enqueue_stale_skips_fresh_slugs(db_session):
    await slug_registry_service.mark_fetched(
        "greenhouse_board", "airbnb", "ok", db_session
    )
    profile = _profile_with_slugs("airbnb", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert queued == ["stripe"]


@pytest.mark.asyncio
async def test_enqueue_stale_skips_invalid_slugs(db_session):
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    profile = _profile_with_slugs("openai", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert queued == ["stripe"]


@pytest.mark.asyncio
async def test_next_pending_claims_and_orders_by_queued_at(db_session):
    profile = _profile_with_slugs("airbnb", "stripe", "notion")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    rows = await slug_registry_service.next_pending(db_session, limit=2)
    assert len(rows) == 2
    assert all(r.claimed_at is not None for r in rows)


@pytest.mark.asyncio
async def test_next_pending_skips_claimed_within_lease(db_session):
    profile = _profile_with_slugs("airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    first = await slug_registry_service.next_pending(db_session, limit=10)
    assert len(first) == 1
    second = await slug_registry_service.next_pending(db_session, limit=10)
    assert second == []  # locked by lease


@pytest.mark.asyncio
async def test_next_pending_reclaims_after_lease_expires(db_session):
    profile = _profile_with_slugs("airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    rows = await slug_registry_service.next_pending(db_session, limit=10)
    # Force-expire the lease
    rows[0].claimed_at = datetime.now(UTC) - timedelta(seconds=600)
    db_session.add(rows[0])
    await db_session.commit()

    again = await slug_registry_service.next_pending(db_session, limit=10, lease_seconds=300)
    assert len(again) == 1
```

- [ ] **Step 2: Run tests to verify they pass** (the prior task already implements the behaviour; this is a coverage backstop)

```bash
uv run pytest tests/integration/test_slug_registry.py -v
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_slug_registry.py
git commit -m "test(slug_registry): enqueue + lease edge cases"
```

---

## PHASE 2 — Match-scope bug fix

Independently shippable. Latent bug: today every active job in the global pool is scored against every profile, regardless of slug membership. Phase 2 fixes that and adds the `score_cached` variant Phase 5 will call from `sync_profile`.

### Task 8: match_service — strict slug-scoped candidate filter

**Files:**
- Modify: `app/services/match_service.py:101-124` (the `if jobs is None:` block)
- Test: `tests/integration/test_match_scoring.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_match_scoring.py`:

```python
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services import match_service


@pytest.mark.asyncio
async def test_score_and_match_filters_by_profile_slugs(db_session):
    """A profile with only ['airbnb'] must NOT see Stripe jobs as candidates,
    even if Stripe jobs exist in the global pool from other users (spec 2026-04-28)."""
    # Two jobs, two companies
    airbnb_job = Job(
        source="greenhouse_board", external_id="a-1", title="X",
        company_name="Airbnb", apply_url="https://x", is_active=True,
    )
    stripe_job = Job(
        source="greenhouse_board", external_id="s-1", title="Y",
        company_name="Stripe", apply_url="https://y", is_active=True,
    )
    db_session.add_all([airbnb_job, stripe_job])
    profile = UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": ["airbnb"]},
    )
    db_session.add(profile)
    await db_session.commit()

    # Patch the LangGraph build_graph so we don't actually call an LLM —
    # we only care about which jobs become Application rows.
    from unittest.mock import AsyncMock, MagicMock
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"scores": []})
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        await match_service.score_and_match(profile, db_session)

    apps = (await db_session.execute(
        sa.select(Application).where(Application.profile_id == profile.id)
    )).scalars().all()
    job_ids = {a.job_id for a in apps}
    assert airbnb_job.id in job_ids
    assert stripe_job.id not in job_ids
```

(Add `import sqlalchemy as sa` at the top if missing.)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_match_scoring.py::test_score_and_match_filters_by_profile_slugs -v
```

Expected: FAIL — both jobs become applications today.

- [ ] **Step 3: Modify `score_and_match` to filter by profile slugs**

In `app/services/match_service.py`, replace the `if jobs is None:` block (currently lines 101-124) with:

```python
    if jobs is None:
        from app.data.slug_company import slug_to_company_name
        slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
        if not slugs:
            return []
        company_names = [slug_to_company_name(s) for s in slugs]

        matched_result = await session.execute(
            select(Application.job_id).where(
                Application.profile_id == profile.id,
                Application.match_score.isnot(None),
            )
        )
        matched_ids = {row[0] for row in matched_result.all()}

        candidates_q = (
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.source == "greenhouse_board",
                Job.company_name.in_(company_names),
            )
            .order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
        )
        if matched_ids:
            candidates_q = candidates_q.where(Job.id.notin_(matched_ids))
        candidates_q = candidates_q.limit(settings.matching_jobs_per_batch)

        all_jobs_result = await session.execute(candidates_q)
        jobs = list(all_jobs_result.scalars().all())
```

- [ ] **Step 4: Run the test + the existing matching tests**

```bash
uv run pytest tests/integration/test_match_scoring.py -v
```

Expected: all PASS, including the new `test_score_and_match_filters_by_profile_slugs`. Update any pre-existing test that depended on cross-slug matching (likely `test_match_scoring.py` fixtures will need their profiles to have the right slugs set — adjust those fixtures).

- [ ] **Step 5: Commit**

```bash
git add app/services/match_service.py tests/integration/test_match_scoring.py
git commit -m "fix(match): strict slug-scoped candidate filter"
```

---

### Task 9: match_service — `score_cached` variant for instant-feedback path

**Files:**
- Modify: `app/services/match_service.py` (add new function)
- Test: `tests/integration/test_match_scoring.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_match_scoring.py`:

```python
@pytest.mark.asyncio
async def test_score_cached_only_uses_existing_jobs(db_session):
    """score_cached must NOT enqueue any fetches and must respect the slug filter
    and matching_jobs_per_batch cap."""
    profile = UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": ["airbnb"]},
    )
    db_session.add(profile)
    db_session.add(Job(
        source="greenhouse_board", external_id="a-2", title="Z",
        company_name="Airbnb", apply_url="https://z", is_active=True,
    ))
    await db_session.commit()

    from unittest.mock import AsyncMock, MagicMock
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"scores": []})
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await match_service.score_cached(profile, db_session, cap=20)
    assert isinstance(result, list)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_match_scoring.py::test_score_cached_only_uses_existing_jobs -v
```

Expected: FAIL with `AttributeError: module 'app.services.match_service' has no attribute 'score_cached'`.

- [ ] **Step 3: Add score_cached**

Append to `app/services/match_service.py`:

```python
async def score_cached(
    profile: UserProfile,
    session: AsyncSession,
    *,
    cap: int | None = None,
) -> list[Application]:
    """Variant of score_and_match that scores at most `cap` already-cached jobs.
    No fetches, no slug-pool growth. Used by the instant-feedback path of POST /api/jobs/sync."""
    from app.config import get_settings
    settings = get_settings()
    cap = cap if cap is not None else settings.matching_jobs_per_batch

    from app.data.slug_company import slug_to_company_name
    slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
    if not slugs:
        return []
    company_names = [slug_to_company_name(s) for s in slugs]

    matched_result = await session.execute(
        select(Application.job_id).where(
            Application.profile_id == profile.id,
            Application.match_score.isnot(None),
        )
    )
    matched_ids = {row[0] for row in matched_result.all()}

    q = (
        select(Job)
        .where(
            Job.is_active.is_(True),
            Job.source == "greenhouse_board",
            Job.company_name.in_(company_names),
        )
        .order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
    )
    if matched_ids:
        q = q.where(Job.id.notin_(matched_ids))
    q = q.limit(cap)
    jobs_result = await session.execute(q)
    jobs = list(jobs_result.scalars().all())
    if not jobs:
        return []
    return await score_and_match(profile, session, jobs=jobs)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_match_scoring.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/match_service.py tests/integration/test_match_scoring.py
git commit -m "feat(match): score_cached variant (slug-scoped, capped)"
```

---

## PHASE 3 — Match queue + default slugs

### Task 10: match_queue_service — enqueue_for_interested_profiles, next_batch

**Files:**
- Create: `app/services/match_queue_service.py`
- Test: `tests/integration/test_match_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_match_queue.py`:

```python
"""Integration tests for the per-(profile, job) match queue."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services import match_queue_service


def _job(slug: str, ext: str = "1") -> Job:
    from app.data.slug_company import slug_to_company_name
    return Job(
        source="greenhouse_board",
        external_id=f"{slug}-{ext}",
        title="Engineer",
        company_name=slug_to_company_name(slug),
        apply_url=f"https://x/{slug}/{ext}",
        is_active=True,
    )


def _profile(*slugs: str) -> UserProfile:
    return UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": list(slugs)},
        search_active=True,
    )


@pytest.mark.asyncio
async def test_enqueue_creates_application_for_each_interested_profile(db_session):
    job = _job("airbnb")
    db_session.add(job)
    p_a = _profile("airbnb", "stripe")
    p_b = _profile("airbnb")
    p_c = _profile("notion")  # not interested
    db_session.add_all([p_a, p_b, p_c])
    await db_session.commit()
    await db_session.refresh(job)

    enqueued = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert enqueued == 2

    apps = (await db_session.execute(
        sa.select(Application).where(Application.job_id == job.id)
    )).scalars().all()
    profile_ids = {a.profile_id for a in apps}
    assert profile_ids == {p_a.id, p_b.id}
    for a in apps:
        assert a.match_status == "pending_match"
        assert a.match_queued_at is not None


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_on_conflict(db_session):
    job = _job("airbnb")
    db_session.add(job)
    p = _profile("airbnb")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(job)

    first = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    second = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert first == 1
    assert second == 0  # ON CONFLICT DO NOTHING


@pytest.mark.asyncio
async def test_enqueue_skips_inactive_profiles(db_session):
    job = _job("airbnb")
    db_session.add(job)
    inactive = _profile("airbnb")
    inactive.search_active = False
    db_session.add(inactive)
    await db_session.commit()

    enqueued = await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    assert enqueued == 0


@pytest.mark.asyncio
async def test_next_batch_claims_oldest_first(db_session):
    p = _profile("airbnb")
    db_session.add(p)
    db_session.add_all([_job("airbnb", str(i)) for i in range(3)])
    await db_session.commit()
    for j in (await db_session.execute(sa.select(Job))).scalars():
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    batch = await match_queue_service.next_batch(db_session, limit=2)
    assert len(batch) == 2
    assert all(a.match_claimed_at is not None for a in batch)


@pytest.mark.asyncio
async def test_mark_done_clears_claim(db_session):
    p = _profile("airbnb")
    db_session.add(p)
    db_session.add(_job("airbnb"))
    await db_session.commit()
    job = (await db_session.execute(sa.select(Job))).scalar_one()
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    [app] = await match_queue_service.next_batch(db_session, limit=10)

    await match_queue_service.mark_done(app.id, db_session)
    refreshed = (await db_session.execute(
        sa.select(Application).where(Application.id == app.id)
    )).scalar_one()
    assert refreshed.match_status == "matched"
    assert refreshed.match_queued_at is None
    assert refreshed.match_claimed_at is None


@pytest.mark.asyncio
async def test_mark_error_after_3_attempts(db_session):
    p = _profile("airbnb")
    db_session.add(p)
    db_session.add(_job("airbnb"))
    await db_session.commit()
    job = (await db_session.execute(sa.select(Job))).scalar_one()
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)
    [app] = await match_queue_service.next_batch(db_session, limit=10)

    for _ in range(3):
        await match_queue_service.mark_attempt_failed(app.id, db_session)
    refreshed = (await db_session.execute(
        sa.select(Application).where(Application.id == app.id)
    )).scalar_one()
    assert refreshed.match_status == "error"
    assert refreshed.match_attempts == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_match_queue.py -v
```

Expected: FAIL with `ModuleNotFoundError: app.services.match_queue_service`.

- [ ] **Step 3: Implement match_queue_service**

Create `app/services/match_queue_service.py`:

```python
"""Per-(profile, job) match work queue.

Lifecycle:
  enqueue_for_interested_profiles → INSERT pending_match rows for every active
                                    profile whose target_company_slugs.greenhouse
                                    contains the job's company.
  next_batch                      → claim oldest pending_match rows.
  mark_done / mark_attempt_failed → terminal transitions.
"""
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.data.slug_company import company_name_to_slug
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile

MAX_ATTEMPTS = 3
log = structlog.get_logger()


async def enqueue_for_interested_profiles(job: Job, session: AsyncSession) -> int:
    """For every active profile whose slug list contains job's company,
    INSERT an Application(match_status='pending_match'). Idempotent on (job_id, profile_id)."""
    slug = company_name_to_slug(job.company_name)
    # Find profiles whose target_company_slugs.greenhouse JSONB array contains this slug.
    # Postgres JSONB containment: target_company_slugs->'greenhouse' @> '"<slug>"'::jsonb
    from sqlalchemy import text
    result = await session.execute(
        text(
            "SELECT id FROM user_profiles "
            "WHERE search_active = true "
            "AND target_company_slugs->'greenhouse' @> :needle::jsonb"
        ),
        {"needle": f'"{slug}"'},
    )
    profile_ids = [row[0] for row in result.all()]
    if not profile_ids:
        return 0

    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid.uuid4(),
            "job_id": job.id,
            "profile_id": pid,
            "match_status": "pending_match",
            "match_queued_at": now,
            "match_attempts": 0,
            "status": "pending_review",
            "generation_status": "none",
            "generation_attempts": 0,
            "match_strengths": [],
            "match_gaps": [],
            "created_at": now,
            "updated_at": now,
        }
        for pid in profile_ids
    ]
    stmt = insert(Application).values(rows).on_conflict_do_nothing(
        index_elements=["job_id", "profile_id"]
    )
    res = await session.execute(stmt)
    await session.commit()
    return res.rowcount or 0


async def next_batch(
    session: AsyncSession, *, limit: int = 30, lease_seconds: int = 300
) -> list[Application]:
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    result = await session.execute(
        select(Application)
        .where(
            Application.match_status == "pending_match",
            (Application.match_claimed_at.is_(None)) | (Application.match_claimed_at < cutoff),
        )
        .order_by(Application.match_queued_at)
        .limit(limit)
    )
    rows = list(result.scalars().all())
    now = datetime.now(UTC)
    for row in rows:
        row.match_claimed_at = now
    if rows:
        await session.commit()
    return rows


async def mark_done(application_id: uuid.UUID, session: AsyncSession) -> None:
    result = await session.execute(
        select(Application).where(Application.id == application_id)
    )
    app = result.scalar_one()
    app.match_status = "matched"
    app.match_queued_at = None
    app.match_claimed_at = None
    await session.commit()


async def mark_attempt_failed(application_id: uuid.UUID, session: AsyncSession) -> None:
    result = await session.execute(
        select(Application).where(Application.id == application_id)
    )
    app = result.scalar_one()
    app.match_attempts += 1
    app.match_claimed_at = None
    if app.match_attempts >= MAX_ATTEMPTS:
        app.match_status = "error"
        app.match_queued_at = None
    await session.commit()


async def pending_count(session: AsyncSession, profile_id: uuid.UUID | None = None) -> int:
    from sqlalchemy import func
    q = select(func.count()).select_from(Application).where(
        Application.match_status == "pending_match"
    )
    if profile_id is not None:
        q = q.where(Application.profile_id == profile_id)
    result = await session.execute(q)
    return int(result.scalar_one())
```

- [ ] **Step 4: Add the new columns to the SQLModel**

Edit `app/models/application.py` to add the four columns just below `generation_attempts`:

```python
    match_status: str = "pending_match"
    match_attempts: int = 0
    match_queued_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    match_claimed_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_match_queue.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/match_queue_service.py app/models/application.py tests/integration/test_match_queue.py
git commit -m "feat(services): match_queue_service (enqueue, claim, mark)"
```

---

### Task 11: Default slugs catalog + nightly validation test

**Files:**
- Create: `app/data/default_slugs.py`
- Test: `tests/unit/test_default_slugs_catalog.py` (offline shape) + `tests/integration/test_default_slugs_live.py` (online — marked nightly)

- [ ] **Step 1: Write the offline test**

`tests/unit/test_default_slugs_catalog.py`:

```python
"""Catalog of curated Greenhouse default slugs.

Online validity is exercised separately by tests/integration/test_default_slugs_live.py
(marked `nightly`). This file only checks shape/uniqueness so PRs aren't blocked
by transient Greenhouse outages."""
from app.data.default_slugs import DEFAULT_SLUGS


def test_catalog_size_in_band():
    assert 10 <= len(DEFAULT_SLUGS) <= 20


def test_all_lowercase_kebab():
    import re
    for slug in DEFAULT_SLUGS:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug), slug


def test_unique():
    assert len(DEFAULT_SLUGS) == len(set(DEFAULT_SLUGS))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_default_slugs_catalog.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 3: Create the catalog**

`app/data/default_slugs.py`:

```python
"""Hand-picked Greenhouse-active companies seeded for users with empty slug lists.

Verified live via tests/integration/test_default_slugs_live.py (nightly cron).
If a slug starts 404'ing, replace it here AND remove from any active profiles.

Selection criteria: large engineering org (≥50 open jobs typical), uses Greenhouse
public board, mix of well-known and high-quality smaller companies.
"""

DEFAULT_SLUGS: list[str] = [
    "airbnb",
    "stripe",
    "dropbox",
    "vercel",
    "instacart",
    "gusto",
    "robinhood",
    "doordash",
    "scaleai",
    "rampnetwork",
    "anthropic",
    "samsara",
    "datadog",
    "cloudflare",
    "asana",
]
```

NOTE: these are *initial picks* — the live test in Step 6 will surface any 404s. The engineer should re-run that test and replace any failing slug with another candidate (consult `https://www.google.com/search?q=site:boards.greenhouse.io+<keyword>` for ideas) before merging the PR. The 15 above were chosen as illustrative, not authoritative.

- [ ] **Step 4: Write the online test (marked nightly)**

`tests/integration/test_default_slugs_live.py`:

```python
"""Online validation of the default-slugs catalog.

Marked `nightly` so it runs on the cron-only CI job, not on every PR
(prevents transient Greenhouse outages from blocking unrelated work)."""
import httpx
import pytest

from app.data.default_slugs import DEFAULT_SLUGS

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards"


@pytest.mark.nightly
@pytest.mark.parametrize("slug", DEFAULT_SLUGS)
def test_default_slug_is_live(slug):
    resp = httpx.get(f"{GREENHOUSE}/{slug}", timeout=10.0)
    assert resp.status_code == 200, (
        f"Default slug `{slug}` returned {resp.status_code} — "
        f"replace it in app/data/default_slugs.py."
    )
```

Register the marker in `pyproject.toml` (search for `[tool.pytest.ini_options]` and add `markers = ["nightly: live external API checks; run only on the nightly CI job"]` if not already there).

- [ ] **Step 5: Run the offline test**

```bash
uv run pytest tests/unit/test_default_slugs_catalog.py -v
```

Expected: all PASS.

- [ ] **Step 6: Run the live test once locally to weed out dead slugs**

```bash
uv run pytest tests/integration/test_default_slugs_live.py -m nightly -v
```

Expected: all PASS. If any FAIL, replace the dead slug in `app/data/default_slugs.py` with a live alternative and re-run.

- [ ] **Step 7: Commit**

```bash
git add app/data/default_slugs.py tests/unit/test_default_slugs_catalog.py tests/integration/test_default_slugs_live.py pyproject.toml
git commit -m "feat(data): default-slugs catalog + nightly live validation"
```

---

## PHASE 4 — Cron workers + cron schedule

### Task 12: `run_sync_queue` worker

**Files:**
- Modify: `app/scheduler/tasks.py` (add new function)
- Test: `tests/integration/test_sync_queue_cron.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_sync_queue_cron.py`:

```python
"""Integration test for run_sync_queue cron worker."""
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_sync_queue
from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


def _profile(*slugs: str) -> UserProfile:
    return UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": list(slugs)},
        search_active=True,
    )


@pytest.mark.asyncio
async def test_run_sync_queue_fetches_claimed_slugs_and_enqueues_matches(db_session):
    profile = _profile("airbnb")
    db_session.add(profile)
    await db_session.commit()
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    fixture = {"jobs": [{
        "id": 9001, "title": "Backend Engineer",
        "location": {"name": "Remote"},
        "absolute_url": "https://boards.greenhouse.io/airbnb/jobs/9001",
        "updated_at": datetime.now(UTC).isoformat(),
        "content": "<p>job</p>",
    }]}
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb/jobs").mock(
            return_value=httpx.Response(200, json=fixture)
        )
        result = await run_sync_queue()

    assert result["fetched"] == 1
    jobs = (await db_session.execute(sa.select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].company_name == slug_to_company_name("airbnb")
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 1
    assert apps[0].match_status == "pending_match"


@pytest.mark.asyncio
async def test_run_sync_queue_marks_invalid_after_2_404s(db_session):
    profile = _profile("openai")
    db_session.add(profile)
    await db_session.commit()
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(
            return_value=httpx.Response(404)
        )
        await run_sync_queue()
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False

    # Re-queue + run again
    row.queued_at = datetime.now(UTC)
    await db_session.commit()
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai/jobs").mock(
            return_value=httpx.Response(404)
        )
        await run_sync_queue()
    row = await slug_registry_service.get("greenhouse_board", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_sync_queue_cron.py -v
```

Expected: FAIL with `ImportError: cannot import name 'run_sync_queue' from 'app.scheduler.tasks'`.

- [ ] **Step 3: Implement run_sync_queue**

Append to `app/scheduler/tasks.py`:

```python
async def run_sync_queue(*, max_slugs: int = 64, deadline_seconds: int = 240) -> dict:
    """Drain the slug fetch queue. Per-tick deadline keeps us under Cloud Run's
    300s wall. Anything not finished is left for the next tick."""
    import asyncio
    import time
    import httpx
    from app.database import get_session_factory
    from app.services import slug_registry_service, job_service, match_queue_service
    from app.sources.greenhouse_board import (
        GreenhouseBoardSource,
        InvalidSlugError,
        TransientFetchError,
        DEFAULT_TIMEOUT,
    )

    factory = get_session_factory()
    deadline = time.monotonic() + deadline_seconds
    counts = {"fetched": 0, "invalid": 0, "transient": 0, "skipped_deadline": 0}

    async with factory() as session:
        claimed = await slug_registry_service.next_pending(session, limit=max_slugs)
    if not claimed:
        return {**counts, "remaining": 0}

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, http2=True) as client:
        source = GreenhouseBoardSource()
        sem = asyncio.Semaphore(8)

        async def _one(row):
            if time.monotonic() > deadline:
                counts["skipped_deadline"] += 1
                return
            async with sem:
                if time.monotonic() > deadline:
                    counts["skipped_deadline"] += 1
                    return
                # Compute `since`: existing slug → last_fetched_at - 1h overlap;
                # new slug (last_fetched_at IS NULL) → now - 14d.
                since = (
                    row.last_fetched_at - timedelta(hours=1)
                    if row.last_fetched_at is not None
                    else datetime.now(UTC) - timedelta(days=14)
                )
                try:
                    jobs = await source.fetch_jobs(row.slug, since=since, client=client)
                except InvalidSlugError as exc:
                    async with factory() as s:
                        await slug_registry_service.mark_fetched(
                            row.source, row.slug, "invalid", s, error=str(exc)
                        )
                    counts["invalid"] += 1
                    return
                except TransientFetchError as exc:
                    async with factory() as s:
                        await slug_registry_service.mark_fetched(
                            row.source, row.slug, "transient_error", s, error=str(exc)
                        )
                    counts["transient"] += 1
                    return

                async with factory() as s:
                    new_count = 0
                    for jd in jobs:
                        job, created = await job_service.upsert_job(jd, row.source, s)
                        if created:
                            new_count += 1
                            await match_queue_service.enqueue_for_interested_profiles(job, s)
                    await slug_registry_service.mark_fetched(
                        row.source, row.slug, "ok", s
                    )
                    await log.ainfo(
                        "slug_fetch.ok",
                        source=row.source, slug=row.slug,
                        new_jobs=new_count, total_jobs=len(jobs),
                    )
                    counts["fetched"] += 1

        await asyncio.gather(*(_one(r) for r in claimed), return_exceptions=False)

    async with factory() as session:
        remaining = await slug_registry_service.pending_count(session)
    return {**counts, "remaining": remaining}
```

Add `from datetime import UTC, datetime, timedelta` to the top of `app/scheduler/tasks.py` if `timedelta` isn't already imported.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_sync_queue_cron.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/tasks.py tests/integration/test_sync_queue_cron.py
git commit -m "feat(scheduler): run_sync_queue worker (concurrent fetch, deadline-bounded)"
```

---

### Task 13: `run_match_queue` worker + endpoint

**Files:**
- Modify: `app/scheduler/tasks.py`, `app/api/internal_cron.py`
- Test: `tests/integration/test_match_queue_cron.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_match_queue_cron.py`:

```python
"""Integration test for run_match_queue cron worker."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_match_queue
from app.services import match_queue_service


@pytest.mark.asyncio
async def test_run_match_queue_drains_pending(db_session):
    profile = UserProfile(
        user_id=uuid.uuid4(),
        target_company_slugs={"greenhouse": ["airbnb"]},
        search_active=True,
    )
    job = Job(
        source="greenhouse_board", external_id="x-1",
        title="Engineer", company_name=slug_to_company_name("airbnb"),
        apply_url="https://x", is_active=True,
    )
    db_session.add_all([profile, job])
    await db_session.commit()
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)

    # Patch the LangGraph build_graph to return a passing score
    fake_graph = MagicMock()
    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult
        return {"scores": [ScoreResult(
            application_id=state["jobs"][0]["application_id"],
            score=0.9, rationale="great fit",
            strengths=["python"], gaps=[],
        )]}
    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue()

    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert apps[0].match_status == "matched"
    assert apps[0].match_score == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_match_queue_cron.py -v
```

Expected: FAIL with `ImportError: cannot import name 'run_match_queue'`.

- [ ] **Step 3: Implement run_match_queue**

Append to `app/scheduler/tasks.py`:

```python
async def run_match_queue(*, batch_size: int = 30, deadline_seconds: int = 240) -> dict:
    """Drain pending_match applications. One LangGraph batch per tick (the agent
    fans out internally). Per-tick deadline keeps us under Cloud Run's 300s wall."""
    import time
    from app.database import get_session_factory
    from app.services import match_queue_service, profile_service
    from app.services.match_service import format_profile_text
    from app.models.application import Application
    from app.models.job import Job
    from app.models.user_profile import UserProfile
    from sqlmodel import select

    factory = get_session_factory()
    deadline = time.monotonic() + deadline_seconds
    attempted = succeeded = failed = 0

    async with factory() as session:
        batch = await match_queue_service.next_batch(session, limit=batch_size)
    if not batch:
        return {"attempted": 0, "succeeded": 0, "failed": 0}
    attempted = len(batch)

    # Group by profile_id; one LangGraph invocation per profile
    by_profile: dict = {}
    for app in batch:
        by_profile.setdefault(app.profile_id, []).append(app)

    for profile_id, apps in by_profile.items():
        if time.monotonic() > deadline:
            break
        async with factory() as session:
            profile = (await session.execute(
                select(UserProfile).where(UserProfile.id == profile_id)
            )).scalar_one()
            jobs = (await session.execute(
                select(Job).where(Job.id.in_([a.job_id for a in apps]))
            )).scalars().all()

            from app.services.match_service import score_and_match
            try:
                scored = await score_and_match(profile, session, jobs=jobs)
            except Exception as exc:
                await log.aexception("match_queue.batch_error", error=str(exc))
                for a in apps:
                    await match_queue_service.mark_attempt_failed(a.id, session)
                failed += len(apps)
                continue

            scored_ids = {a.id for a in scored}
            for a in apps:
                # If it has a score now, mark done. If still no score (rate-limited),
                # increment attempts; will retry next tick.
                refreshed = (await session.execute(
                    select(Application).where(Application.id == a.id)
                )).scalar_one()
                if refreshed.match_score is not None or refreshed.status == "auto_rejected":
                    await match_queue_service.mark_done(refreshed.id, session)
                    succeeded += 1
                else:
                    await match_queue_service.mark_attempt_failed(refreshed.id, session)
                    failed += 1

    return {"attempted": attempted, "succeeded": succeeded, "failed": failed}
```

- [ ] **Step 4: Add the cron endpoints**

In `app/api/internal_cron.py`, append before the existing `cron_maintenance` route:

```python
@router.post("/process-sync-queue", dependencies=[Depends(verify_secret)])
async def cron_process_sync_queue():
    from app.scheduler.tasks import run_sync_queue
    return await _run_cron("process_sync_queue", run_sync_queue)


@router.post("/process-match-queue", dependencies=[Depends(verify_secret)])
async def cron_process_match_queue():
    from app.scheduler.tasks import run_match_queue
    return await _run_cron("process_match_queue", run_match_queue)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_match_queue_cron.py tests/integration/test_cron_endpoints.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/scheduler/tasks.py app/api/internal_cron.py tests/integration/test_match_queue_cron.py
git commit -m "feat(scheduler): run_match_queue + /internal/cron/process-{sync,match}-queue"
```

---

### Task 14: Bulk-enqueue from /internal/cron/sync (replace per-profile fetch loop)

**Files:**
- Modify: `app/scheduler/tasks.py:run_job_sync`
- Test: `tests/integration/test_sync_queue_cron.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_sync_queue_cron.py`:

```python
@pytest.mark.asyncio
async def test_run_job_sync_bulk_enqueues_for_active_profiles(db_session):
    """The 6h /internal/cron/sync becomes a bulk-enqueue: it does not fetch directly
    but seeds the slug_fetches queue for every active profile's stale slugs."""
    from app.scheduler.tasks import run_job_sync

    p_active = _profile("airbnb", "stripe")
    p_inactive = _profile("notion")
    p_inactive.search_active = False
    db_session.add_all([p_active, p_inactive])
    await db_session.commit()

    summary = await run_job_sync()
    assert summary["profiles_enqueued"] == 1
    # airbnb + stripe queued (notion skipped because profile is inactive)
    pending = await slug_registry_service.pending_count(db_session)
    assert pending == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_sync_queue_cron.py::test_run_job_sync_bulk_enqueues_for_active_profiles -v
```

Expected: FAIL — old `run_job_sync` returns `profiles_synced`, not `profiles_enqueued`.

- [ ] **Step 3: Rewrite run_job_sync**

Replace `run_job_sync()` in `app/scheduler/tasks.py` with:

```python
async def run_job_sync() -> dict:
    """Bulk-enqueue stale slugs for every active profile. The actual fetch
    happens in run_sync_queue; this is just the scheduled "wake up and sweep" pass."""
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services import slug_registry_service

    factory = get_session_factory()
    profiles_enqueued = 0
    slugs_enqueued = 0
    async with factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        for profile in result.scalars().all():
            queued = await slug_registry_service.enqueue_stale(
                profile, session, ttl_hours=6
            )
            if queued:
                profiles_enqueued += 1
                slugs_enqueued += len(queued)
    return {
        "profiles_enqueued": profiles_enqueued,
        "slugs_enqueued": slugs_enqueued,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_sync_queue_cron.py tests/integration/test_cron_endpoints.py -v
```

Expected: all PASS. The pre-existing `test_cron_endpoints.py` may need its `run_job_sync` expectations updated.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/tasks.py tests/integration/
git commit -m "refactor(scheduler): /internal/cron/sync becomes bulk-enqueue"
```

---

### Task 15: GitHub Actions cron — add 15-min queue drains

**Files:**
- Modify: `.github/workflows/cron.yml`

- [ ] **Step 1: Add the new schedules**

Edit `.github/workflows/cron.yml`. Replace the `on:` block and add two new jobs:

```yaml
on:
  schedule:
    - cron: '0 */6 * * *'      # bulk-enqueue stale slugs
    - cron: '*/10 * * * *'     # generation queue (existing)
    - cron: '0 3 * * *'        # daily maintenance (existing)
    - cron: '*/15 * * * *'     # process sync + match queues (new)
  workflow_dispatch:
```

Update the `sync` job condition: change `'0 */4 * * *'` to `'0 */6 * * *'`.

Add two new jobs at the end of the file (paste below the `maintenance` job):

```yaml
  process-sync-queue:
    if: github.event.schedule == '*/15 * * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger sync queue drain
        env:
          CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
          CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
        run: |
          response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
            "$CLOUD_RUN_URL/internal/cron/process-sync-queue")
          body=$(echo "$response" | head -n -1)
          code=$(echo "$response" | tail -n 1)
          echo "HTTP $code"
          echo "$body"
          [ "$code" -lt 400 ] || exit 1

  process-match-queue:
    if: github.event.schedule == '*/15 * * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger match queue drain
        env:
          CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
          CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
        run: |
          response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
            "$CLOUD_RUN_URL/internal/cron/process-match-queue")
          body=$(echo "$response" | head -n -1)
          code=$(echo "$response" | tail -n 1)
          echo "HTTP $code"
          echo "$body"
          [ "$code" -lt 400 ] || exit 1
```

- [ ] **Step 2: Validate YAML locally**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/cron.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/cron.yml
git commit -m "ci(cron): drain sync + match queues every 15 min, sweep every 6h"
```

---

## PHASE 5 — Sync flow rewrite + status endpoint

### Task 16: profile_service.seed_defaults_if_empty helper

**Files:**
- Modify: `app/services/profile_service.py`
- Test: `tests/unit/test_profile_service.py` (create if it doesn't exist; otherwise extend)

- [ ] **Step 1: Write the failing test**

Add (or create) `tests/unit/test_profile_service.py`:

```python
import uuid
from app.models.user_profile import UserProfile
from app.services.profile_service import seed_defaults_if_empty


def test_seed_defaults_if_empty_seeds_first_5():
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {}
    changed = seed_defaults_if_empty(p)
    assert changed is True
    assert len(p.target_company_slugs["greenhouse"]) == 5


def test_seed_defaults_if_empty_no_op_when_slugs_present():
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {"greenhouse": ["custom-co"]}
    changed = seed_defaults_if_empty(p)
    assert changed is False
    assert p.target_company_slugs["greenhouse"] == ["custom-co"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_profile_service.py -v
```

Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Add the helper**

Append to `app/services/profile_service.py`:

```python
def seed_defaults_if_empty(profile) -> bool:
    """If profile has no greenhouse slugs, seed the first 5 from the curated catalog.
    Returns True if seeded, False if no-op. Mutates profile in place; caller commits."""
    from app.data.default_slugs import DEFAULT_SLUGS
    existing = (profile.target_company_slugs or {}).get("greenhouse", [])
    if existing:
        return False
    profile.target_company_slugs = {
        **(profile.target_company_slugs or {}),
        "greenhouse": DEFAULT_SLUGS[:5],
    }
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_profile_service.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/profile_service.py tests/unit/test_profile_service.py
git commit -m "feat(profile): seed_defaults_if_empty helper"
```

---

### Task 17: Rewrite job_sync_service.sync_profile (enqueue-only + score-cached)

**Files:**
- Modify: `app/services/job_sync_service.py` (full rewrite)
- Test: `tests/integration/test_job_sync.py` (rewrite the test for the new contract)

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_sync_profile_with_mocked_source` in `tests/integration/test_job_sync.py` (and any related test) with the new contract:

```python
@pytest.mark.asyncio
async def test_sync_profile_returns_202_shape_and_enqueues_stale_slugs(db_session):
    """The new contract: sync_profile is enqueue-only + score-cached, returns
    {status:'queued', queued_slugs:[...], matched_now:int}, never blocks on fetch."""
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    profile.target_company_slugs = {"greenhouse": ["airbnb", "stripe"]}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)

    assert result["status"] == "queued"
    assert sorted(result["queued_slugs"]) == ["airbnb", "stripe"]
    assert result["matched_now"] == 0  # no cached jobs yet


@pytest.mark.asyncio
async def test_sync_profile_seeds_defaults_when_empty(db_session):
    from app.models.user import User
    from app.services.profile_service import get_or_create_profile

    user = User(id=uuid.uuid4(), email="t@t.com")
    db_session.add(user)
    await db_session.commit()
    profile = await get_or_create_profile(user.id, db_session)
    # explicitly empty
    profile.target_company_slugs = {}
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    result = await job_sync_service.sync_profile(profile, db_session)
    assert result["seeded_defaults"] is True
    assert len(result["queued_slugs"]) == 5
    await db_session.refresh(profile)
    assert len(profile.target_company_slugs["greenhouse"]) == 5
```

(Delete or rewrite `test_sync_profile_no_slugs_short_circuits` — it doesn't apply anymore.)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_job_sync.py -v
```

Expected: FAIL — current `sync_profile` returns `{new_jobs, updated_jobs, ...}`.

- [ ] **Step 3: Rewrite sync_profile**

Replace the body of `app/services/job_sync_service.py` with:

```python
"""Job sync entrypoint — enqueueing-only, fast.

The actual fetch happens in app.scheduler.tasks.run_sync_queue.
The actual matching happens in app.scheduler.tasks.run_match_queue.
This function:
  1. Seeds 5 default slugs if profile has none.
  2. Enqueues every stale (last_fetched_at NULL or > 6h old) slug for background fetch.
  3. Scores up to `matching_jobs_per_batch` already-cached, slug-scoped, unscored jobs
     so the user sees something immediately.
"""
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user_profile import UserProfile
from app.services import match_service, slug_registry_service
from app.services.profile_service import seed_defaults_if_empty

log = structlog.get_logger()


async def sync_profile(profile: UserProfile, session: AsyncSession) -> dict:
    settings = get_settings()
    seeded = seed_defaults_if_empty(profile)
    if seeded:
        session.add(profile)
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    matched = await match_service.score_cached(
        profile, session, cap=settings.matching_jobs_per_batch
    )

    summary = {
        "queued_slugs": queued,
        "matched_now": len(matched),
        "seeded_defaults": seeded,
    }
    profile.last_sync_requested_at = datetime.now(UTC)
    profile.last_sync_summary = summary
    if not queued:
        # Nothing to fetch — sync is "complete" right now.
        profile.last_sync_completed_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()

    await log.ainfo(
        "sync.queued",
        profile_id=str(profile.id),
        queued_slugs=queued,
        matched_now=len(matched),
        seeded_defaults=seeded,
    )
    return {"status": "queued", **summary}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_job_sync.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/job_sync_service.py tests/integration/test_job_sync.py
git commit -m "refactor(sync): sync_profile is enqueue-only + score-cached"
```

---

### Task 18: POST /api/jobs/sync returns 202; remove dead background-task code

**Files:**
- Modify: `app/api/jobs.py` (rewrite)
- Test: `tests/unit/test_jobs_api_quota.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_jobs_api_quota.py`:

```python
@pytest.mark.asyncio
async def test_sync_endpoint_returns_202(client, auth_headers):
    """New contract: POST /api/jobs/sync returns 202 with the queued summary,
    not 200 with synchronous results."""
    response = await client.post("/api/jobs/sync", headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert "queued_slugs" in body
    assert "matched_now" in body
```

(If `client` and `auth_headers` aren't already in this file's imports, copy the imports and fixtures from a sibling test e.g. `tests/integration/test_jobs_endpoint.py`.)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_jobs_api_quota.py::test_sync_endpoint_returns_202 -v
```

Expected: FAIL — current endpoint returns 200.

- [ ] **Step 3: Rewrite the endpoint**

Replace `app/api/jobs.py` with:

```python
"""Jobs sync and query endpoints."""
import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user_profile import UserProfile
from app.services import job_sync_service
from app.services.rate_limit_service import check_daily_quota

log = structlog.get_logger()
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MANUAL_SYNC_DAILY_LIMIT = 25


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Manual user-initiated sync: enqueues stale slugs + scores cached jobs.
    Returns 202 immediately. Background fetch + match catches up via cron."""
    if settings.environment == "production":
        await check_daily_quota(profile.user_id, "manual_sync", MANUAL_SYNC_DAILY_LIMIT, session)
    result = await job_sync_service.sync_profile(profile, session)
    return JSONResponse(status_code=202, content=result)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_jobs_api_quota.py tests/integration/test_job_sync.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/jobs.py tests/unit/test_jobs_api_quota.py
git commit -m "feat(api): POST /api/jobs/sync returns 202; remove dead BackgroundTasks"
```

---

### Task 19: GET /api/sync/status endpoint

**Files:**
- Create: `tests/integration/test_sync_status_endpoint.py`
- Modify: `app/api/jobs.py` (add the GET route)

- [ ] **Step 1: Write the failing test**

`tests/integration/test_sync_status_endpoint.py`:

```python
"""GET /api/sync/status — used by the dashboard chip to poll progress."""
import uuid
import pytest
import sqlalchemy as sa

from app.models.application import Application
from app.models.job import Job
from app.models.slug_fetch import SlugFetch
from app.services import slug_registry_service


@pytest.mark.asyncio
async def test_status_idle_when_nothing_queued(client, auth_headers, seeded_user):
    response = await client.get("/api/sync/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["slugs_pending"] == 0
    assert body["matches_pending"] == 0
    assert body["invalid_slugs"] == []


@pytest.mark.asyncio
async def test_status_syncing_when_user_slug_queued(
    client, auth_headers, seeded_user, db_session
):
    _, profile = seeded_user
    profile.target_company_slugs = {"greenhouse": ["airbnb"]}
    db_session.add(profile)
    await db_session.commit()
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["state"] == "syncing"
    assert body["slugs_pending"] == 1


@pytest.mark.asyncio
async def test_status_lists_invalid_slugs(client, auth_headers, seeded_user, db_session):
    _, profile = seeded_user
    profile.target_company_slugs = {"greenhouse": ["openai"]}
    db_session.add(profile)
    await db_session.commit()
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse_board", "openai", "invalid", db_session)

    response = await client.get("/api/sync/status", headers=auth_headers)
    body = response.json()
    assert body["invalid_slugs"] == ["openai"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_sync_status_endpoint.py -v
```

Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add the endpoint**

Append to `app/api/jobs.py`:

```python
from datetime import UTC, datetime
from sqlalchemy import func
from sqlmodel import select

from app.models.application import Application
from app.models.slug_fetch import SlugFetch


@router.get("/sync/status")
async def sync_status(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    user_slugs: list[str] = (profile.target_company_slugs or {}).get("greenhouse", []) or []

    slugs_pending = 0
    invalid_slugs: list[str] = []
    if user_slugs:
        rows = (await session.execute(
            select(SlugFetch).where(
                SlugFetch.source == "greenhouse_board",
                SlugFetch.slug.in_(user_slugs),
            )
        )).scalars().all()
        for r in rows:
            if r.is_invalid:
                invalid_slugs.append(r.slug)
            elif r.queued_at is not None:
                slugs_pending += 1

    matches_pending = int((await session.execute(
        select(func.count()).select_from(Application).where(
            Application.profile_id == profile.id,
            Application.match_status == "pending_match",
        )
    )).scalar_one())

    if slugs_pending > 0:
        state = "syncing"
    elif matches_pending > 0:
        state = "matching"
    else:
        state = "idle"

    return {
        "state": state,
        "slugs_total": len(user_slugs),
        "slugs_pending": slugs_pending,
        "matches_pending": matches_pending,
        "last_sync_requested_at": profile.last_sync_requested_at.isoformat()
            if profile.last_sync_requested_at else None,
        "last_sync_completed_at": profile.last_sync_completed_at.isoformat()
            if profile.last_sync_completed_at else None,
        "last_sync_summary": profile.last_sync_summary,
        "invalid_slugs": sorted(invalid_slugs),
    }
```

Also add the new sync columns to `app/models/user_profile.py`:

```python
    last_sync_requested_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_sync_completed_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_sync_summary: dict | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_sync_status_endpoint.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/jobs.py app/models/user_profile.py tests/integration/test_sync_status_endpoint.py
git commit -m "feat(api): GET /api/sync/status (state + counts + invalid slugs)"
```

---

## PHASE 6 — Onboarding guardrail + frontend + cleanup

### Task 20: Onboarding agent — validate slugs before persisting

**Files:**
- Modify: `app/agents/onboarding.py` (the place where target_company_slugs is written)
- Test: `tests/integration/test_onboarding_agent.py` (extend)

- [ ] **Step 1: Locate the slug-writing code**

```bash
grep -n "target_company_slugs" app/agents/onboarding.py
```

Note the function and line where slugs land on the profile.

- [ ] **Step 2: Write the failing test**

Append to `tests/integration/test_onboarding_agent.py`:

```python
@pytest.mark.asyncio
async def test_onboarding_filters_invalid_slugs(db_session, monkeypatch):
    """The onboarding agent must call validate_slug for each inferred slug
    and persist only the ones that exist on Greenhouse."""
    from app.services import slug_registry_service

    seen = []
    async def fake_validate(source, slug, session):
        seen.append(slug)
        return slug != "openai"  # openai is dead; everything else valid

    monkeypatch.setattr(slug_registry_service, "validate_slug", fake_validate)

    # Drive the flow that writes slugs onto the profile.
    # (Adjust the call to match the agent's actual API.)
    from app.agents.onboarding import persist_inferred_slugs
    profile = await _make_profile(db_session)
    await persist_inferred_slugs(
        profile, ["airbnb", "openai", "stripe"], db_session
    )
    await db_session.refresh(profile)

    assert profile.target_company_slugs["greenhouse"] == ["airbnb", "stripe"]
    assert "openai" in seen
```

(`persist_inferred_slugs` is the function name we'll create in the next step. If the onboarding agent currently writes slugs in a different shape, refactor that write into a single helper named `persist_inferred_slugs(profile, slugs, session)` first.)

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_onboarding_agent.py -v -k filters_invalid
```

Expected: FAIL — function doesn't exist (or the existing code persists all slugs without validation).

- [ ] **Step 4: Implement the validating write**

In `app/agents/onboarding.py`, add (or extract from existing logic) the helper:

```python
async def persist_inferred_slugs(profile, slugs: list[str], session) -> list[str]:
    """Validate each slug against Greenhouse before persisting. Returns the
    list of slugs that survived validation."""
    from app.services import slug_registry_service
    valid: list[str] = []
    for s in slugs:
        if await slug_registry_service.validate_slug("greenhouse_board", s, session):
            valid.append(s)
    profile.target_company_slugs = {
        **(profile.target_company_slugs or {}),
        "greenhouse": valid,
    }
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return valid
```

Replace the existing direct write to `profile.target_company_slugs["greenhouse"]` in the agent with a call to this helper.

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_onboarding_agent.py -v
```

Expected: all PASS. Update any other onboarding test that relied on slugs being written without validation.

- [ ] **Step 6: Commit**

```bash
git add app/agents/onboarding.py tests/integration/test_onboarding_agent.py
git commit -m "feat(onboarding): validate inferred slugs before persisting"
```

---

### Task 21: Frontend — sync button toast + status chip

**Files:**
- Create: `frontend/src/components/SyncStatusChip.tsx`
- Modify: the dashboard page that contains the "Sync now" button

- [ ] **Step 1: Find the existing sync button**

```bash
grep -rn "jobs/sync\|Sync now\|syncJobs\|sync_jobs" frontend/src 2>/dev/null
```

Note the file and component (likely `frontend/src/pages/Dashboard.tsx` or `frontend/src/pages/Matches.tsx`).

- [ ] **Step 2: Update the click handler to expect 202 + show toast**

Edit the existing handler. The new shape:

```tsx
async function onSyncClick() {
  setSyncing(true);
  try {
    const res = await fetch("/api/jobs/sync", {
      method: "POST",
      credentials: "include",
    });
    if (res.status === 202) {
      const body = await res.json();
      toast(
        `Searching now. ${body.matched_now} matches from cache, ${body.queued_slugs.length} boards queued. New matches will appear in a couple minutes.`
      );
    } else if (res.status === 429) {
      toast.error("Daily sync quota reached. Try again tomorrow.");
    } else {
      toast.error(`Sync failed (${res.status}).`);
    }
  } finally {
    setSyncing(false);
  }
}
```

(If the codebase uses a different toast library, adapt the call.)

- [ ] **Step 3: Add the SyncStatusChip component**

`frontend/src/components/SyncStatusChip.tsx`:

```tsx
import { useEffect, useState } from "react";

type Status = {
  state: "idle" | "syncing" | "matching";
  slugs_total: number;
  slugs_pending: number;
  matches_pending: number;
  last_sync_summary: { matched_now?: number } | null;
  invalid_slugs: string[];
};

export function SyncStatusChip({ onIdle }: { onIdle?: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    let cancelled = false;
    let prevState: string | null = null;
    async function poll() {
      try {
        const res = await fetch("/api/sync/status", { credentials: "include" });
        if (!res.ok) return;
        const body: Status = await res.json();
        if (cancelled) return;
        setStatus(body);
        if (prevState && prevState !== "idle" && body.state === "idle") {
          onIdle?.();
        }
        prevState = body.state;
      } catch {
        // Network error — keep polling silently
      }
    }
    poll();
    const id = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [onIdle]);

  if (!status || status.state === "idle") return null;
  const text =
    status.state === "syncing"
      ? `Syncing ${status.slugs_pending} of ${status.slugs_total} boards`
      : `Scoring ${status.matches_pending} job${status.matches_pending === 1 ? "" : "s"}`;
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-sm text-blue-700">
      <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
      {text}
    </span>
  );
}
```

- [ ] **Step 4: Mount the chip in the dashboard**

In the dashboard component, import and render `<SyncStatusChip onIdle={() => refetchMatches()} />` next to the Sync button. Replace `refetchMatches` with the actual matches-list refetch hook in your code.

- [ ] **Step 5: Smoke-test in the browser**

```bash
# In one terminal
docker compose up -d db
uv run uvicorn app.main:app --reload --port 8000
# In another terminal
cd frontend && npm run dev
```

Open `http://localhost:5173`, log in, click Sync — confirm: toast appears immediately, chip shows "Syncing N of M boards" then "Scoring X jobs" then disappears, matches list refreshes.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/
git commit -m "feat(ui): sync toast + polling status chip"
```

---

### Task 22: Frontend — invalid slugs notice

**Files:**
- Create: `frontend/src/components/InvalidSlugsNotice.tsx`
- Modify: dashboard page (mount the notice)

- [ ] **Step 1: Build the notice**

`frontend/src/components/InvalidSlugsNotice.tsx`:

```tsx
import { useEffect, useState } from "react";

export function InvalidSlugsNotice() {
  const [invalid, setInvalid] = useState<string[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch("/api/sync/status", { credentials: "include" });
        if (!res.ok) return;
        const body = await res.json();
        if (!cancelled) setInvalid(body.invalid_slugs ?? []);
      } catch {}
    }
    load();
    const id = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const visible = invalid.filter((s) => !dismissed.has(s));
  if (visible.length === 0) return null;

  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      We removed{" "}
      {visible.map((s, i) => (
        <span key={s}>
          <code className="font-mono">{s}</code>
          {i < visible.length - 1 ? ", " : ""}
        </span>
      ))}{" "}
      — Greenhouse no longer has boards for{" "}
      {visible.length === 1 ? "it" : "them"}.{" "}
      <button
        className="ml-2 underline"
        onClick={() => setDismissed(new Set([...dismissed, ...visible]))}
      >
        Dismiss
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Mount it on the dashboard**

Import and render `<InvalidSlugsNotice />` near the top of the dashboard page (same component as the chip).

- [ ] **Step 3: Smoke-test**

Manually create an invalid slug in the dev DB:

```bash
docker compose exec db psql -U postgres -d postgres -c \
  "INSERT INTO slug_fetches(source, slug, is_invalid, invalid_reason, consecutive_404_count) \
   VALUES ('greenhouse_board', 'fakecorp', true, 'test', 2) \
   ON CONFLICT DO NOTHING;"
```

Then add `fakecorp` to your test profile's slugs (via the UI or psql) and reload the dashboard — confirm the notice appears.

Cleanup:

```bash
docker compose exec db psql -U postgres -d postgres -c \
  "DELETE FROM slug_fetches WHERE slug = 'fakecorp';"
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/
git commit -m "feat(ui): show notice for auto-pruned invalid slugs"
```

---

### Task 23: Cleanup — remove old per-profile-iteration code

**Files:**
- Modify: `app/services/job_sync_service.py` (already gone after Task 17, but verify), `app/scheduler/tasks.py`

- [ ] **Step 1: Search for any stragglers**

```bash
grep -rn "InvalidSlugError\|TransientFetchError" app/ | grep -v test
grep -rn "_score_after_sync\|background_tasks" app/api/jobs.py
grep -rn "_dedup\|_normalize" app/services/job_sync_service.py
```

Anything that ONLY existed for the old per-profile sync loop is now dead. Specifically:
- `_dedup` and `_normalize` in old `job_sync_service.py` — gone after Task 17.
- `_score_after_sync` and the `BackgroundTasks` import in `app/api/jobs.py` — gone after Task 18.
- `mark_stale_jobs` is still used by `run_daily_maintenance` — keep it.
- `InvalidSlugError`/`TransientFetchError` are still raised by `GreenhouseBoardSource` and consumed by `run_sync_queue` — keep them.

- [ ] **Step 2: Run the full test suite as a regression check**

```bash
uv run pytest tests/unit/ tests/integration/ -v
```

Expected: all PASS. If anything fails, the failing test points to a missing wiring or an outdated expectation — fix in place.

- [ ] **Step 3: Run ruff and any type-checker the project uses**

```bash
uv run ruff check app/ tests/
```

Expected: clean. Fix any unused-imports left over from the rewrites.

- [ ] **Step 4: Commit**

```bash
git add -u app/ tests/
git commit -m "chore: remove dead code from old per-profile sync loop"
```

---

## Self-Review

Spec coverage check (run yourself):

| Spec section | Implemented in |
|---|---|
| `slug_fetches` table | Task 1 (migration) + Task 2 (model) |
| `match_status` columns on Application | Task 1 + Task 10 (model field) |
| Sync visibility columns on UserProfile | Task 1 + Task 19 (model field) |
| 21d staleness | Task 3 |
| `slug_registry_service` | Tasks 6 + 7 |
| `match_queue_service` | Task 10 |
| `default_slugs` catalog | Task 11 |
| `GreenhouseBoardSource.validate()` + shared client + `fetch_jobs(since)` | Task 5 |
| Strict slug-scoped match (latent bug fix) | Task 8 |
| `score_cached` | Task 9 |
| `run_sync_queue` worker | Task 12 |
| `run_match_queue` worker | Task 13 |
| `/internal/cron/process-sync-queue`, `process-match-queue` | Task 13 |
| Bulk-enqueue `run_job_sync` | Task 14 |
| Cron schedule update | Task 15 |
| `seed_defaults_if_empty` | Task 16 |
| Rewritten `sync_profile` | Task 17 |
| `POST /api/jobs/sync` returns 202 | Task 18 |
| `GET /api/sync/status` | Task 19 |
| Onboarding slug validation | Task 20 |
| Frontend toast + chip | Task 21 |
| Frontend invalid-slugs notice | Task 22 |
| Cleanup | Task 23 |

All spec sections have a task. No placeholders remain. Type names verified consistent across tasks (`SlugFetch`, `match_status='pending_match'|'matched'|'error'`, `score_cached`, `run_sync_queue`, `run_match_queue`).

---

## Notes for the executor

- **Branch & deployment.** Phases 1-3 (Tasks 1-9) ship as one PR ("foundation + match-scope bug fix") — they don't change any API contract. Phases 4-6 (Tasks 10-23) ship as a second PR ("queue-driven sync"); the API contract change (200→202) means **frontend must merge in lockstep with backend**.
- **Database migration is forward-safe.** Adding columns + tables, no destructive ops. The new `match_status` column is backfilled in the same migration. Roll out in this order: deploy migration → deploy backend → deploy frontend.
- **First post-deploy cron tick.** The migration's backfill seeds `slug_fetches` with one row per distinct slug across all active profiles, all with `last_fetched_at IS NULL`. The first `process-sync-queue` tick will fetch all of them. Expect a brief spike of Greenhouse API calls — bounded by the 8-way semaphore × 4-min budget per tick × number of slugs.
- **If a test fails because `score_and_match`'s old fixture-driven tests assumed cross-slug matching**, update those fixtures so each profile's slug list contains the test job's company. The bug fix is intentional; the old behaviour was wrong.
