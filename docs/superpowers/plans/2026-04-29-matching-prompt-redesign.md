# Matching Prompt Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-04-29-matching-prompt-redesign-design.md`

**Goal:** Replace free-form `match_rationale` (UI) with a 1-line `match_summary`; reframe `strengths` as JD requirements met; tighten output for fewer tokens; fix the missing-locations data leak; pre-clean job descriptions to markdown at ingestion via a new `description_clean` column.

**Architecture:** Two nullable Postgres columns (`jobs.description_clean`, `applications.match_summary`); a new `app/services/html_cleaner.py` module wrapping `markdownify`; matching agent prompt split into SystemMessage + HumanMessage with a calibrated grading rubric and per-field word caps; `format_profile_text()` always emits a `Locations` line; `JobContext` carries structured location/workplace_type. Backward-compatible during the rollout window: matching falls back to raw `description_md` until a one-off backfill populates `description_clean`.

**Tech Stack:** Python (uv), FastAPI, SQLModel + Alembic, LangGraph, Gemini 2.5 Flash, markdownify, pytest + testcontainers, React + Vitest.

---

## Notes for the implementer

- **Migrations:** ALWAYS use `make migrate ARGS="..."` or `uv run python scripts/alembic_safe.py ...`. NEVER plain `alembic`. The wrapper blocks write commands against non-local hosts unless `I_KNOW_ITS_PROD=1` is set; this is exactly the outage mode of commit `28e5ce5`. Local dev uses `docker compose up -d db` first.
- **Env for local DB:** `POSTGRES_USER=jobagent`, `POSTGRES_DB=jobagent`. Container name `job-application-agent-db-1`.
- **Tests with `ENVIRONMENT=test`** auto-substitute `FakeListChatModel` for the LLM (`app/agents/test_llm.py`). No real API key needed.
- **Model registration:** Any new model must be imported in `app/models/__init__.py` so `alembic/env.py` sees it. (We're not adding new models — just columns — so this is informational.)
- **Frontend:** dev server `cd frontend && npm run dev` (port 5173, proxies to :8000). Tests: `npm run test` (Vitest).
- **Lint hooks:** `ruff check` and `ruff format --check` run on writes. Stay under 100 chars/line.

---

## Task order summary

1. HTML cleaner module + unit tests
2. Alembic migration: `jobs.description_clean`
3. Wire cleaner into `upsert_job` + integration test
4. Backfill script + integration test
5. Alembic migration: `applications.match_summary`
6. Profile text rendering: always-include Locations
7. Matching agent: prompt rewrite, tool-args + ScoreResult update, JobContext + location fields, fake LLM update
8. `match_service`: pass location fields, persist `match_summary`, prefer `description_clean`
9. API serializer: add `match_summary` to both endpoints
10. Frontend: type + display swap (rationale → summary)
11. End-to-end smoke verification

---

## Task 1: HTML cleaner module

**Files:**
- Create: `app/services/html_cleaner.py`
- Test: `tests/unit/test_html_cleaner.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/unit/test_html_cleaner.py`:

```python
"""Unit tests for app.services.html_cleaner."""

from app.services.html_cleaner import clean_html_to_markdown


def test_returns_empty_string_for_none_input():
    assert clean_html_to_markdown(None) == ""


def test_returns_empty_string_for_empty_input():
    assert clean_html_to_markdown("") == ""


def test_strips_html_tags_and_returns_markdown():
    html = "<h2>Requirements</h2><ul><li><strong>Python</strong> 5+ years</li></ul>"
    out = clean_html_to_markdown(html)
    assert "## Requirements" in out
    assert "**Python**" in out
    assert "5+ years" in out
    assert "<h2>" not in out
    assert "<li>" not in out


def test_drops_script_and_style_tags():
    html = "<p>visible</p><script>alert(1)</script><style>.x{}</style>"
    out = clean_html_to_markdown(html)
    assert "visible" in out
    assert "alert" not in out
    assert ".x{}" not in out


def test_collapses_excessive_blank_lines():
    html = "<p>a</p>\n\n\n\n<p>b</p>"
    out = clean_html_to_markdown(html)
    assert "\n\n\n" not in out
    assert "a" in out and "b" in out


def test_already_markdown_input_is_idempotent_enough():
    md = "## Hello\n\n* item one\n* item two\n"
    out = clean_html_to_markdown(md)
    # Markdownify on already-markdown is a near no-op; must not lose content.
    assert "Hello" in out
    assert "item one" in out
    assert "item two" in out
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_html_cleaner.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.html_cleaner'`

- [ ] **Step 1.3: Write the minimal implementation**

Create `app/services/html_cleaner.py`:

```python
"""HTML → markdown cleaner for job descriptions.

Wraps markdownify with sane defaults (ATX headings, drop script/style),
plus a whitespace pass to collapse runs of >2 blank lines.

Intended consumer: app.services.job_service.upsert_job, which writes
description_clean alongside the raw description_md.
"""

import re

from markdownify import markdownify


def clean_html_to_markdown(html: str | None) -> str:
    """Convert raw HTML to compact markdown. Returns '' for None/empty input."""
    if not html:
        return ""
    md = markdownify(html, heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", md).strip()
```

- [ ] **Step 1.4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_html_cleaner.py -v
```

Expected: 6 passed.

- [ ] **Step 1.5: Commit**

```bash
git add app/services/html_cleaner.py tests/unit/test_html_cleaner.py
git commit -m "feat(matching): add HTML→markdown cleaner module"
```

---

## Task 2: Migration — `jobs.description_clean` column

**Files:**
- Create: `alembic/versions/<timestamp>_add_job_description_clean.py` (alembic generates the filename)
- Modify: `app/models/job.py`

- [ ] **Step 2.1: Generate the migration**

```bash
make migrate ARGS="revision -m 'add_job_description_clean' --autogenerate"
```

Note: this requires the local DB up (`docker compose up -d db`) and migrations to be at HEAD. If autogenerate produces extra noise (e.g., dropping a checkpoint table), discard those edits — the migration must contain only the `description_clean` column add.

Expected: a new file `alembic/versions/<timestamp>_add_job_description_clean.py` with `op.add_column("jobs", sa.Column("description_clean", sa.Text(), nullable=True))` in `upgrade()` and the inverse in `downgrade()`.

- [ ] **Step 2.2: Verify migration content**

Open the generated file and confirm `upgrade()` is exactly:

```python
def upgrade() -> None:
    op.add_column("jobs", sa.Column("description_clean", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "description_clean")
```

If autogenerate added unrelated ops (e.g., touching `checkpoint_*` tables), delete them — the LangGraph saver owns those.

- [ ] **Step 2.3: Add the column to the SQLModel**

Modify `app/models/job.py` — add the field next to `description_md`:

```python
class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str  # adzuna, greenhouse_board, jsearch, remoteok, remotive
    external_id: str
    title: str
    company_name: str
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_md: str | None = None
    description_clean: str | None = None  # markdown, populated at ingestion by html_cleaner
    salary: str | None = None
    # ... rest unchanged
```

- [ ] **Step 2.4: Apply the migration locally**

```bash
make migrate ARGS="upgrade head"
```

Expected: alembic logs `Running upgrade … -> <new>, add_job_description_clean`.

- [ ] **Step 2.5: Verify the column exists**

```bash
docker exec job-application-agent-db-1 psql -U jobagent -d jobagent -c "\d jobs" | grep description
```

Expected output includes `description_clean | text |` line.

- [ ] **Step 2.6: Commit**

```bash
git add alembic/versions/ app/models/job.py
git commit -m "feat(matching): add jobs.description_clean column"
```

---

## Task 3: Wire cleaner into `upsert_job`

**Files:**
- Modify: `app/services/job_service.py`
- Test: `tests/integration/test_job_sync.py`

- [ ] **Step 3.1: Write the failing integration test**

Add to `tests/integration/test_job_sync.py` (append to existing file; if the file doesn't have an existing fixture for `JobData`/`session`, mirror what's already there):

```python
import pytest

from app.models.job import Job
from app.services import job_service
from app.sources.base import JobData
from sqlmodel import select


@pytest.mark.asyncio
async def test_upsert_job_populates_description_clean(db_session):
    """upsert_job should compute description_clean from raw HTML description_md."""
    raw = "<h2>About</h2><ul><li><strong>Python</strong></li></ul>"
    data = JobData(
        external_id="ext-clean-1",
        title="Test Engineer",
        company_name="Test Co",
        location="Remote",
        workplace_type="remote",
        description_md=raw,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/1",
        posted_at=None,
    )
    job, created = await job_service.upsert_job(data, "greenhouse_board", db_session)
    assert created is True
    assert job.description_clean is not None
    assert "## About" in job.description_clean
    assert "**Python**" in job.description_clean
    assert "<h2>" not in job.description_clean


@pytest.mark.asyncio
async def test_upsert_job_recomputes_description_clean_on_update(db_session):
    """Re-upserting an existing job recomputes description_clean."""
    data = JobData(
        external_id="ext-clean-2",
        title="Test Engineer",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_md="<p>v1</p>",
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/2",
        posted_at=None,
    )
    await job_service.upsert_job(data, "greenhouse_board", db_session)

    data.description_md = "<p>v2 updated</p>"
    job, created = await job_service.upsert_job(data, "greenhouse_board", db_session)
    assert created is False
    assert "v2 updated" in (job.description_clean or "")
    assert "v1" not in (job.description_clean or "")


@pytest.mark.asyncio
async def test_upsert_job_handles_none_description(db_session):
    """A job with no description should store description_clean='' (or None — both safe)."""
    data = JobData(
        external_id="ext-clean-3",
        title="Title only",
        company_name="Test Co",
        location=None,
        workplace_type=None,
        description_md=None,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/3",
        posted_at=None,
    )
    job, _ = await job_service.upsert_job(data, "greenhouse_board", db_session)
    assert job.description_clean in ("", None)
```

The `db_session` fixture is provided by `tests/integration/conftest.py:44` (per-test async session against testcontainers Postgres) — no need to import it.

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
uv run pytest tests/integration/test_job_sync.py -k "description_clean" -v
```

Expected: tests fail because `description_clean` is never set (it'll be `None`, but the markdown assertion will fail).

- [ ] **Step 3.3: Update `upsert_job` to populate `description_clean`**

Modify `app/services/job_service.py`:

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
    On conflict (source + external_id): update title, description, is_active, fetched_at.
    description_clean is recomputed from description_md on every write.
    """
    result = await session.execute(
        select(Job).where(
            Job.source == source,
            Job.external_id == job_data.external_id,
        )
    )
    existing = result.scalar_one_or_none()

    cleaned = clean_html_to_markdown(job_data.description_md)

    if existing:
        existing.title = job_data.title
        existing.company_name = job_data.company_name
        existing.description_md = job_data.description_md
        existing.description_clean = cleaned
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
        description_md=job_data.description_md,
        description_clean=cleaned,
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

- [ ] **Step 3.4: Run the test to verify it passes**

```bash
uv run pytest tests/integration/test_job_sync.py -k "description_clean" -v
```

Expected: 3 passed.

- [ ] **Step 3.5: Run the full integration suite to catch regressions**

```bash
uv run pytest tests/integration/test_job_sync.py -v
```

Expected: all green.

- [ ] **Step 3.6: Commit**

```bash
git add app/services/job_service.py tests/integration/test_job_sync.py
git commit -m "feat(matching): populate description_clean in upsert_job"
```

---

## Task 4: Backfill script for legacy rows

**Files:**
- Create: `scripts/backfill_job_description_clean.py`
- Test: `tests/integration/test_backfill_description_clean.py`

- [ ] **Step 4.1: Write the failing integration test**

Create `tests/integration/test_backfill_description_clean.py`:

```python
"""Backfill script populates description_clean for rows where it's NULL."""

import pytest
from sqlmodel import select

from app.models.job import Job
from scripts.backfill_job_description_clean import run_backfill


@pytest.mark.asyncio
async def test_backfill_populates_null_rows(db_session):
    # Insert rows with description_md but NULL description_clean (simulating legacy state)
    j1 = Job(
        source="greenhouse_board",
        external_id="bf-1",
        title="t1",
        company_name="c1",
        apply_url="https://x/1",
        description_md="<p>hello <strong>one</strong></p>",
        description_clean=None,
    )
    j2 = Job(
        source="greenhouse_board",
        external_id="bf-2",
        title="t2",
        company_name="c2",
        apply_url="https://x/2",
        description_md="<p>hello two</p>",
        description_clean="already-set",  # should NOT be touched
    )
    j3 = Job(
        source="greenhouse_board",
        external_id="bf-3",
        title="t3",
        company_name="c3",
        apply_url="https://x/3",
        description_md=None,
        description_clean=None,  # NULL desc_md → backfill should set '' or skip
    )
    db_session.add_all([j1, j2, j3])
    await db_session.commit()

    processed, skipped = await run_backfill(batch_size=10, session=db_session)

    # Re-read
    result = await db_session.execute(select(Job).where(Job.external_id.in_(["bf-1", "bf-2", "bf-3"])))
    by_id = {j.external_id: j for j in result.scalars().all()}

    assert "**one**" in (by_id["bf-1"].description_clean or "")
    assert by_id["bf-2"].description_clean == "already-set"  # untouched
    # bf-3: description_md is NULL — backfill writes '' so NOT NULL anymore
    assert by_id["bf-3"].description_clean == ""
    assert processed >= 2
```

- [ ] **Step 4.2: Run the test to verify it fails**

```bash
uv run pytest tests/integration/test_backfill_description_clean.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.backfill_job_description_clean'`.

- [ ] **Step 4.3: Write the backfill script**

Create `scripts/backfill_job_description_clean.py`:

```python
"""Backfill jobs.description_clean for rows where it's NULL.

Idempotent: re-running is safe (only touches NULL rows).

Usage:
    uv run python scripts/backfill_job_description_clean.py [--batch-size 200]
"""

import argparse
import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.database import get_session_factory
from app.models.job import Job
from app.services.html_cleaner import clean_html_to_markdown

log = structlog.get_logger()


async def run_backfill(batch_size: int, session: AsyncSession) -> tuple[int, int]:
    """Process all NULL description_clean rows in batches. Returns (processed, skipped)."""
    processed = 0
    skipped = 0
    while True:
        result = await session.execute(
            select(Job)
            .where(Job.description_clean.is_(None))
            .limit(batch_size)
        )
        rows = list(result.scalars().all())
        if not rows:
            break
        for job in rows:
            try:
                job.description_clean = clean_html_to_markdown(job.description_md)
                session.add(job)
                processed += 1
            except Exception as exc:
                await log.aerror(
                    "backfill.row_failed", external_id=job.external_id, error=str(exc)
                )
                skipped += 1
        await session.commit()
        await log.ainfo("backfill.batch", processed=processed, skipped=skipped)
    return processed, skipped


async def main(batch_size: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        processed, skipped = await run_backfill(batch_size, session)
    print(f"Backfill complete. processed={processed} skipped={skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(main(args.batch_size))
```

- [ ] **Step 4.4: Run the test to verify it passes**

```bash
uv run pytest tests/integration/test_backfill_description_clean.py -v
```

Expected: 1 passed.

- [ ] **Step 4.5: Commit**

```bash
git add scripts/backfill_job_description_clean.py tests/integration/test_backfill_description_clean.py
git commit -m "feat(matching): add backfill script for jobs.description_clean"
```

---

## Task 5: Migration — `applications.match_summary`

**Files:**
- Create: `alembic/versions/<timestamp>_add_application_match_summary.py`
- Modify: `app/models/application.py`

- [ ] **Step 5.1: Generate the migration**

```bash
make migrate ARGS="revision -m 'add_application_match_summary' --autogenerate"
```

- [ ] **Step 5.2: Verify migration content**

Confirm `upgrade()` is exactly:

```python
def upgrade() -> None:
    op.add_column("applications", sa.Column("match_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "match_summary")
```

Discard any unrelated edits autogenerate added.

- [ ] **Step 5.3: Add the field to the SQLModel**

Modify `app/models/application.py` — add `match_summary` next to `match_rationale`:

```python
    match_score: float | None = None
    match_summary: str | None = None  # 1-line job summary (UI). Single-writer: matching agent.
    match_rationale: str | None = None  # short audit-only rationale (not surfaced in UI).
    match_strengths: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(sa.String)))
    match_gaps: list[str] = Field(default_factory=list, sa_column=Column(ARRAY(sa.String)))
```

- [ ] **Step 5.4: Apply the migration locally**

```bash
make migrate ARGS="upgrade head"
```

- [ ] **Step 5.5: Verify the column exists**

```bash
docker exec job-application-agent-db-1 psql -U jobagent -d jobagent -c "\d applications" | grep match_
```

Expected output includes `match_summary | text |` and `match_rationale | text |` lines.

- [ ] **Step 5.6: Commit**

```bash
git add alembic/versions/ app/models/application.py
git commit -m "feat(matching): add applications.match_summary column"
```

---

## Task 6: Profile text — always include `Locations`

**Files:**
- Modify: `app/services/match_service.py` (function `format_profile_text`, lines 22–59)
- Test: `tests/unit/test_match_service.py`

- [ ] **Step 6.1: Write the failing tests**

Add to `tests/unit/test_match_service.py` (append to existing file):

```python
from datetime import date
from unittest.mock import MagicMock

from app.services.match_service import format_profile_text


def _profile(target_locations=None, remote_ok=False, full_name=None, seniority=None):
    p = MagicMock()
    p.target_locations = target_locations or []
    p.remote_ok = remote_ok
    p.full_name = full_name
    p.seniority = seniority
    p.target_roles = []
    p.base_resume_md = None
    return p


def test_profile_text_includes_locations_with_cities_and_remote():
    p = _profile(target_locations=["San Francisco", "San Jose"], remote_ok=True)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Locations: San Francisco, San Jose; remote: yes" in text


def test_profile_text_includes_locations_with_cities_no_remote():
    p = _profile(target_locations=["New York"], remote_ok=False)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Locations: New York; remote: no" in text


def test_profile_text_remote_only_renders_explicit_none():
    p = _profile(target_locations=[], remote_ok=True)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Locations: (none); remote: yes" in text


def test_profile_text_no_remote_no_locations_still_renders():
    """A profile with neither cities nor remote should still emit the line so the LLM never has to infer."""
    p = _profile(target_locations=[], remote_ok=False)
    text = format_profile_text(p, skills=[], experiences=[])
    assert "Locations: (none); remote: no" in text
```

- [ ] **Step 6.2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_match_service.py -k "profile_text" -v
```

Expected: assertion errors — current code emits only `"Open to remote: yes"` when `remote_ok=True` and never emits Locations.

- [ ] **Step 6.3: Update `format_profile_text`**

Modify `app/services/match_service.py`, function `format_profile_text` (lines 22–59). Replace the body so the Locations line is unconditional and the old `Open to remote` line is removed (subsumed):

```python
def format_profile_text(
    profile: UserProfile,
    skills: list[Skill],
    experiences: list[WorkExperience],
) -> str:
    """Render profile as markdown text for LLM consumption.

    Always emits a 'Locations:' line so the matching LLM never has to
    infer the candidate's location stance from the absence of a field.
    """
    lines = []
    if profile.full_name:
        lines.append(f"# {profile.full_name}")
    if profile.seniority:
        lines.append(f"Seniority: {profile.seniority}")
    if profile.target_roles:
        lines.append(f"Target roles: {', '.join(profile.target_roles)}")

    locs = list(profile.target_locations or [])
    locs_str = ", ".join(locs) if locs else "(none)"
    remote_str = "yes" if profile.remote_ok else "no"
    lines.append(f"Locations: {locs_str}; remote: {remote_str}")

    if skills:
        lines.append("\n## Skills")
        by_category: dict[str, list[str]] = {}
        for s in skills:
            cat = s.category or "other"
            by_category.setdefault(cat, []).append(s.name)
        for cat, names in by_category.items():
            lines.append(f"- {cat}: {', '.join(names)}")

    if experiences:
        lines.append("\n## Work Experience")
        for exp in experiences:
            end = exp.end_date.year if exp.end_date else "present"
            lines.append(f"### {exp.title} at {exp.company} ({exp.start_date.year}–{end})")
            if exp.description_md:
                lines.append(exp.description_md[:500])

    if profile.base_resume_md:
        lines.append("\n## Resume")
        lines.append(profile.base_resume_md[:3000])

    return "\n".join(lines)
```

- [ ] **Step 6.4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_match_service.py -k "profile_text" -v
```

Expected: 4 passed.

- [ ] **Step 6.5: Run the full match_service unit suite for regressions**

```bash
uv run pytest tests/unit/test_match_service.py -v
```

Expected: all green.

- [ ] **Step 6.6: Commit**

```bash
git add app/services/match_service.py tests/unit/test_match_service.py
git commit -m "fix(matching): always include Locations line in profile text"
```

---

## Task 7: Matching agent — prompt rewrite, tool args, ScoreResult, JobContext, fake LLM

**Files:**
- Modify: `app/agents/matching_agent.py`
- Modify: `app/agents/test_llm.py`
- Test: `tests/unit/test_matching_agent.py` (new file — confirm it doesn't already exist with `ls tests/unit/test_matching_*`)

- [ ] **Step 7.1: Write the failing unit tests**

Create `tests/unit/test_matching_agent.py`:

```python
"""Unit tests for app.agents.matching_agent prompt and schema shape."""

from app.agents.matching_agent import (
    JobContext,
    ScoreResult,
    SCORING_SYSTEM_PROMPT,
    SCORING_USER_TEMPLATE,
)


def test_system_prompt_contains_grading_rubric():
    assert "0.9" in SCORING_SYSTEM_PROMPT
    assert "Grading" in SCORING_SYSTEM_PROMPT


def test_system_prompt_contains_location_rule():
    assert "Location" in SCORING_SYSTEM_PROMPT
    # Anti-hedge directive
    assert "Decide" in SCORING_SYSTEM_PROMPT
    assert "may require clarification" in SCORING_SYSTEM_PROMPT


def test_system_prompt_documents_all_output_fields():
    for field in ("summary", "strengths", "gaps", "rationale"):
        assert field in SCORING_SYSTEM_PROMPT


def test_user_template_includes_location_line():
    rendered = SCORING_USER_TEMPLATE.format(
        profile_text="profile",
        title="t",
        company="c",
        location="Berlin",
        workplace_type="hybrid",
        description="d",
    )
    assert "Berlin" in rendered
    assert "hybrid" in rendered
    assert "Location:" in rendered


def test_score_result_has_summary_field():
    sr = ScoreResult(
        application_id="00000000-0000-0000-0000-000000000000",
        score=0.8,
        summary="Senior backend role, Python+AWS, hybrid NYC.",
        rationale="Strong stack fit",
        strengths=["5+ yrs Python"],
        gaps=["Onsite NYC, candidate based in CA"],
    )
    assert sr.summary == "Senior backend role, Python+AWS, hybrid NYC."


def test_job_context_has_location_fields():
    ctx: JobContext = {
        "application_id": "x",
        "title": "t",
        "company": "c",
        "location": "Berlin",
        "workplace_type": "hybrid",
        "description": "d",
    }
    assert ctx["location"] == "Berlin"
    assert ctx["workplace_type"] == "hybrid"
```

- [ ] **Step 7.2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_matching_agent.py -v
```

Expected: ImportError on `SCORING_SYSTEM_PROMPT` / `SCORING_USER_TEMPLATE`.

- [ ] **Step 7.3: Rewrite `app/agents/matching_agent.py`**

Replace the file with:

```python
"""
Matching agent — LangGraph StateGraph with Send-based fan-out.

Graph: load_context → fan_out (Send) → score_job (×N parallel) → persist_results

Uses Flash for cost efficiency. Prompt is split into a stable SystemMessage
(grading rubric + output rules — Gemini implicit cache prefix) and a
per-call HumanMessage carrying the profile and the job-specific content.
"""

import asyncio
import operator
from typing import Annotated

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, field_validator
from typing_extensions import TypedDict

from app.agents.llm_safe import BudgetExhausted, safe_ainvoke
from app.config import get_settings

log = structlog.get_logger()

MAX_JOB_DESC_CHARS = 8000


def truncate_description(desc: str, max_chars: int = MAX_JOB_DESC_CHARS) -> str:
    if not desc or len(desc) <= max_chars:
        return desc or ""
    return desc[:max_chars] + "\n\n[Description truncated]"


class ScoreResult(BaseModel):
    application_id: str
    score: float | None  # 0.0 – 1.0; None signals scoring was skipped (retry next sync)
    summary: str          # ≤12 words; UI display
    rationale: str        # ≤20 words; audit only
    strengths: list[str]  # 1-3 JD-met items
    gaps: list[str]       # 1-3 missing/weak items

    @field_validator("strengths", "gaps", mode="before")
    @classmethod
    def coerce_to_list(cls, v: object) -> list[str]:
        """Flash sometimes returns bullet-point strings instead of JSON arrays."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            items = [line.lstrip("-•* \t").strip() for line in v.splitlines() if line.strip()]
            return [item for item in items if item]
        return []


class JobContext(TypedDict):
    application_id: str
    title: str
    company: str
    location: str | None
    workplace_type: str | None
    description: str


class MatchState(TypedDict):
    profile_id: str
    profile_text: str
    jobs: list[JobContext]
    scores: Annotated[list[ScoreResult], operator.add]


class SingleJobState(TypedDict):
    profile_text: str
    job: JobContext


def get_llm():
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm

        return get_fake_llm("matching")
    return ChatGoogleGenerativeAI(
        model=settings.llm_matching_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )


SCORING_SYSTEM_PROMPT = """\
Score how the candidate profile matches the job (0.0-1.0).

Grading:
- 0.9-1.0: meets all required + most preferred
- 0.7-0.89: meets all required, some preferred gaps
- 0.5-0.69: meets most required, notable gaps
- 0.3-0.49: meets some required, major gaps
- 0.0-0.29: fundamental mismatch

Location:
- JD location is in candidate locations OR (JD remote AND candidate remote): not a gap.
- Otherwise: hard gap, e.g., "Onsite Seattle, candidate based in CA".
- Never say "may require clarification" or "depends". Decide.

Output (call record_score):
- summary: <=12 words. The JOB: level, stack, mode. No prose.
- strengths: 1-3 JD requirements the candidate meets. <=8 words each. No filler.
- gaps: 1-3 weak/missing JD requirements. <=8 words each. No filler.
- rationale: <=20 words. Why this score (audit)."""


SCORING_USER_TEMPLATE = """\
PROFILE:
{profile_text}

JOB: {title} @ {company}
Location: {location} · {workplace_type}
{description}"""


def build_graph() -> StateGraph:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.matching_max_concurrency)

    @tool
    def record_score(
        score: float,
        summary: str,
        rationale: str,
        strengths: list[str],
        gaps: list[str],
    ) -> str:
        """Record the match score for this job application."""
        return "Score recorded"

    tools = [record_score]
    llm = get_llm().bind_tools(tools, tool_choice="record_score")

    def load_context_node(state: MatchState) -> dict:
        return {}

    def fan_out(state: MatchState) -> list[Send]:
        return [
            Send("score_job", {"profile_text": state["profile_text"], "job": job})
            for job in state["jobs"]
        ]

    async def score_job_node(state: SingleJobState) -> dict:
        job = state["job"]
        user_prompt = SCORING_USER_TEMPLATE.format(
            profile_text=state["profile_text"],
            title=job["title"],
            company=job["company"],
            location=job["location"] or "unspecified",
            workplace_type=job["workplace_type"] or "unspecified",
            description=truncate_description(job["description"]),
        )
        run_config = {
            "run_name": f"score-{job['company'][:20]}-{job['title'][:30]}",
            "metadata": {"application_id": job["application_id"]},
        }
        # Retry loop: handle transient API rate limit errors with backoff.
        # BudgetExhausted (monthly quota) is NOT retried — it is caught and
        # converted to score=None so the entire matching run is not aborted.
        # Falls back to score=None after exhausting retries for transient errors.
        backoffs = [10, 30]
        for attempt, backoff in enumerate([0] + backoffs):
            if backoff:
                await asyncio.sleep(backoff)
            async with semaphore:
                await asyncio.sleep(0.5)  # throttle: ~6 req/s per slot
                try:
                    result = await safe_ainvoke(
                        llm,
                        [
                            SystemMessage(content=SCORING_SYSTEM_PROMPT),
                            HumanMessage(content=user_prompt),
                        ],
                        config=run_config,
                    )
                    break
                except BudgetExhausted:
                    log.warning("match.budget_exhausted_skip", title=job["title"])
                    return {
                        "scores": [
                            ScoreResult(
                                application_id=job["application_id"],
                                score=None,
                                summary="",
                                rationale="Skipped: LLM quota exhausted",
                                strengths=[],
                                gaps=[],
                            )
                        ]
                    }
                except Exception as exc:
                    is_rate_limit = "429" in str(exc) or "rate_limit" in str(exc).lower()
                    if is_rate_limit and attempt < len(backoffs):
                        continue
                    if is_rate_limit:
                        log.warning(
                            "match.rate_limit_skip",
                            title=job["title"],
                            attempts=attempt + 1,
                        )
                        return {
                            "scores": [
                                ScoreResult(
                                    application_id=job["application_id"],
                                    score=None,
                                    summary="",
                                    rationale="Skipped: API rate limit exceeded after retries",
                                    strengths=[],
                                    gaps=[],
                                )
                            ]
                        }
                    raise

        tool_call = result.tool_calls[0] if result.tool_calls else {}
        args = tool_call.get("args", {}) if tool_call else {}

        score_result = ScoreResult(
            application_id=job["application_id"],
            score=float(args.get("score", 0.0)),
            summary=args.get("summary", ""),
            rationale=args.get("rationale", ""),
            strengths=args.get("strengths", []),
            gaps=args.get("gaps", []),
        )
        return {"scores": [score_result]}

    async def persist_results_node(state: MatchState) -> dict:
        return {}

    builder = StateGraph(MatchState)
    builder.add_node("load_context", load_context_node)
    builder.add_node("score_job", score_job_node)
    builder.add_node("persist_results", persist_results_node)
    builder.set_entry_point("load_context")
    builder.add_conditional_edges("load_context", fan_out, ["score_job"])
    builder.add_edge("score_job", "persist_results")
    builder.add_edge("persist_results", END)

    return builder.compile()
```

- [ ] **Step 7.4: Update the fake LLM response**

Modify `app/agents/test_llm.py`, line 14–16: change the `"matching"` response to include the new `summary` field:

```python
    "matching": [
        '{"score": 0.75, "summary": "Backend role, Python+FastAPI, remote.", '
        '"rationale": "Stack fits; no Go", "strengths": ["Python 5+ yrs"], '
        '"gaps": ["No Go experience"]}',
    ],
```

- [ ] **Step 7.5: Run the unit tests to verify they pass**

```bash
uv run pytest tests/unit/test_matching_agent.py tests/unit/test_match_service.py tests/unit/test_matching_concurrency.py -v
```

Expected: all green. The concurrency test should still pass — the tool args changed but the graph wiring is identical.

- [ ] **Step 7.6: Commit**

```bash
git add app/agents/matching_agent.py app/agents/test_llm.py tests/unit/test_matching_agent.py
git commit -m "feat(matching): rewrite prompt with rubric, summary field, location rule"
```

---

## Task 8: `match_service` — pass location, prefer cleaned description, persist `match_summary`

**Files:**
- Modify: `app/services/match_service.py`
- Test: `tests/integration/test_match_scoring.py`

- [ ] **Step 8.1: Read current `score_and_match` to understand the JobContext build site**

Read `app/services/match_service.py` lines 137–205. The relevant sites are:

- **Lines 154–161** — current `JobContext` literal (only has 4 keys: `application_id`, `title`, `company`, `description`):

  ```python
  job_contexts.append(
      {
          "application_id": str(app.id),
          "title": job.title,
          "company": job.company_name,
          "description": job.description_md or "",
      }
  )
  ```

- **Lines 202–205** — current persist block:

  ```python
  app.match_score = score_result.score
  app.match_rationale = score_result.rationale
  app.match_strengths = score_result.strengths
  app.match_gaps = score_result.gaps
  ```

- [ ] **Step 8.2: Write the failing integration test**

The existing file already provides helpers `_seed_profile(db_session)` and `_seed_job(db_session, ...)` and uses a `patch_llm("app.agents.matching_agent", responses)` context manager imported from `tests.conftest`. Add this test at the end of `tests/integration/test_match_scoring.py`:

```python
@pytest.mark.asyncio
async def test_score_and_match_persists_summary_and_uses_location(db_session):
    """Scored Application gets match_summary populated and rationale stays for audit."""
    profile = await _seed_profile(db_session)
    job = Job(
        source="greenhouse_board",
        external_id=str(uuid.uuid4()),
        title="Senior Backend Engineer",
        company_name="Test Co",
        location="Berlin, Germany",
        workplace_type="hybrid",
        description_md="<p>5+ yrs Python required.</p>",
        description_clean="5+ yrs Python required.",
        apply_url="https://example.com/apply/ms",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    responses = [
        '{"score": 0.85, "summary": "Senior backend, Python, hybrid Berlin.", '
        '"rationale": "Strong stack fit", "strengths": ["5+ yrs Python"], '
        '"gaps": ["Hybrid Berlin, candidate based in CA"]}'
    ]
    with patch_llm("app.agents.matching_agent", responses):
        await score_and_match(profile, db_session, jobs=[job])

    result = await db_session.execute(
        select(Application).where(Application.job_id == job.id)
    )
    app = result.scalar_one()
    assert app.match_summary == "Senior backend, Python, hybrid Berlin."
    assert app.match_rationale == "Strong stack fit"
    assert app.match_score == 0.85
    assert "Hybrid Berlin" in (app.match_gaps or [""])[0]
```

- [ ] **Step 8.3: Run the test to verify it fails**

```bash
uv run pytest tests/integration/test_match_scoring.py -k "summary" -v
```

Expected: AttributeError or assertion failure (`match_summary` is None because nothing populates it yet).

- [ ] **Step 8.4: Update `match_service.score_and_match` and the persist block**

In `app/services/match_service.py`:

(a) Replace lines 154–161 (the `JobContext` literal) with:

```python
job_contexts.append(
    {
        "application_id": str(app.id),
        "title": job.title,
        "company": job.company_name,
        "location": job.location,
        "workplace_type": job.workplace_type,
        "description": job.description_clean or job.description_md or "",
    }
)
```

(b) Replace lines 202–205 (the persist block) with:

```python
app.match_score = score_result.score
app.match_summary = score_result.summary
app.match_rationale = score_result.rationale
app.match_strengths = score_result.strengths
app.match_gaps = score_result.gaps
```

- [ ] **Step 8.5: Run the test to verify it passes**

```bash
uv run pytest tests/integration/test_match_scoring.py -k "summary" -v
```

Expected: 1 passed.

- [ ] **Step 8.6: Run the full match_scoring suite for regressions**

```bash
uv run pytest tests/integration/test_match_scoring.py tests/integration/test_match_queue.py -v
```

Expected: all green.

- [ ] **Step 8.7: Commit**

```bash
git add app/services/match_service.py tests/integration/test_match_scoring.py
git commit -m "feat(matching): persist match_summary, pass JD location to JobContext"
```

---

## Task 9: API — serialize `match_summary` in both endpoints

**Files:**
- Modify: `app/api/applications.py` (lines 38–60 and 79–116)
- Test: `tests/integration/test_apply_lifecycle.py` or similar — confirm with `grep -l "match_rationale" tests/integration/`

- [ ] **Step 9.1: Write the failing test**

Add a new test file `tests/integration/test_applications_api_summary.py` (small, focused — uses the existing helpers from `test_match_scoring.py` to seed):

```python
"""GET /api/applications and /api/applications/{id} expose match_summary."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app as fastapi_app
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile
from tests.integration.test_match_scoring import _seed_profile


@pytest.mark.asyncio
async def test_list_endpoint_includes_match_summary(db_session, auth_headers, seeded_user):
    """List endpoint returns match_summary alongside score/strengths/gaps."""
    _user, profile = seeded_user

    job = Job(
        source="greenhouse_board",
        external_id=str(uuid.uuid4()),
        title="API Test Engineer",
        company_name="API Co",
        apply_url="https://example.com/apply",
        description_md="A role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    db_session.add(
        Application(
            job_id=job.id,
            profile_id=profile.id,
            status="pending_review",
            match_score=0.8,
            match_summary="One-line summary text.",
            match_rationale="Audit text.",
            match_strengths=["Python"],
            match_gaps=["Go"],
        )
    )
    await db_session.commit()

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/applications", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    row = next(r for r in rows if r["match_score"] == 0.8)
    assert row["match_summary"] == "One-line summary text."
    assert row["match_rationale"] == "Audit text."  # still serialized for API audit
```

If `auth_headers` or `seeded_user` aren't available in this directory, copy them from `tests/integration/conftest.py` lines 80–107 — but they're already shared via conftest scoping, so the import-free use should work.

- [ ] **Step 9.2: Run the test to verify it fails**

```bash
uv run pytest tests/integration/test_jobs_endpoint.py -k "match_summary" -v
```

Expected: KeyError or assertion failure (`match_summary` not in serialized dict).

- [ ] **Step 9.3: Update both serializer blocks in `app/api/applications.py`**

In the GET `/api/applications` (list) handler (around line 37–60), add `match_summary`:

```python
result.append(
    {
        "id": str(app.id),
        "status": app.status,
        "generation_status": app.generation_status,
        "match_score": app.match_score,
        "match_summary": app.match_summary,
        "match_rationale": app.match_rationale,
        "match_strengths": app.match_strengths,
        "match_gaps": app.match_gaps,
        "created_at": app.created_at,
        # ... job dict unchanged
    }
)
```

In GET `/api/applications/{app_id}` (around lines 79–88), add the same line:

```python
return {
    "id": str(app.id),
    "status": app.status,
    "generation_status": app.generation_status,
    "generation_attempts": app.generation_attempts,
    "match_score": app.match_score,
    "match_summary": app.match_summary,
    "match_rationale": app.match_rationale,
    "match_strengths": app.match_strengths,
    "match_gaps": app.match_gaps,
    # ... rest unchanged
}
```

- [ ] **Step 9.4: Run the test to verify it passes**

```bash
uv run pytest tests/integration/test_jobs_endpoint.py tests/integration/test_apply_lifecycle.py -v
```

Expected: all green.

- [ ] **Step 9.5: Commit**

```bash
git add app/api/applications.py tests/integration/
git commit -m "feat(matching): serialize match_summary in applications API"
```

---

## Task 10: Frontend — type, MatchCard, ApplicationReview, tests

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/MatchCard.tsx`
- Modify: `frontend/src/components/MatchCard.test.tsx`
- Modify: `frontend/src/pages/ApplicationReview.tsx`

- [ ] **Step 10.1: Add `match_summary` to the TypeScript type**

In `frontend/src/api/client.ts` (around line 60–65 where `match_rationale` lives):

```ts
  match_score: number | null
  match_summary: string | null
  match_rationale: string | null
  match_strengths: string[]
  match_gaps: string[]
```

- [ ] **Step 10.2: Update `MatchCard.test.tsx` fixture and assertions (TDD: red first)**

In `frontend/src/components/MatchCard.test.tsx`, change the `makeApp` factory to use `match_summary` instead of (or alongside) `match_rationale`, and update the assertion to look for the summary text:

```tsx
function makeApp(overrides: Partial<Application> = {}): Application {
  return {
    id: 'app-1',
    status: 'pending_review',
    generation_status: 'none',
    match_score: 0.85,
    match_summary: 'Backend role, Python+FastAPI, remote.',
    match_rationale: 'Strong stack fit',
    match_strengths: ['Python', 'FastAPI'],
    match_gaps: ['Go experience'],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: {
      id: 'job-1',
      title: 'Python Engineer',
      company_name: 'Acme Corp',
      location: 'Remote',
      workplace_type: 'remote',
      salary: '$120k',
      contract_type: 'full-time',
      description_md: 'A great role.',
      apply_url: 'https://example.com/apply',
      posted_at: null,
    },
    ...overrides,
  }
}
```

Add a new test that proves the summary is rendered and the rationale is NOT (the rationale should no longer appear in the card UI):

```tsx
it('renders the match summary and not the rationale', () => {
  renderCard(makeApp())
  expect(screen.getByText('Backend role, Python+FastAPI, remote.')).toBeInTheDocument()
  expect(screen.queryByText('Strong stack fit')).not.toBeInTheDocument()
})
```

- [ ] **Step 10.3: Run frontend tests to verify they fail**

```bash
cd frontend && npm run test -- --run MatchCard
```

Expected: the new "renders the match summary" test fails.

- [ ] **Step 10.4: Update `MatchCard.tsx` to display `match_summary` instead of `match_rationale`**

In `frontend/src/components/MatchCard.tsx`, lines 81–83:

```tsx
{app.match_summary && (
  <p className="mt-3 text-sm text-gray-600">{app.match_summary}</p>
)}
```

(Drop `line-clamp-2` since the summary is already short by design.)

- [ ] **Step 10.5: Run the test to verify it passes**

```bash
cd frontend && npm run test -- --run MatchCard
```

Expected: all MatchCard tests green.

- [ ] **Step 10.6: Update `ApplicationReview.tsx`**

In `frontend/src/pages/ApplicationReview.tsx`, find the rationale display block around line 224 (`{app.match_rationale}`) and swap it to `match_summary`:

```tsx
<span className="font-medium">
  {Math.round(r.match_score * 100)}% match:{' '}
</span>
{r.match_summary}
```

(If the surrounding code uses a different variable name like `app` vs `r`, match what's there.)

- [ ] **Step 10.7: Run frontend tests for any regressions**

```bash
cd frontend && npm run test -- --run
```

Expected: all green.

- [ ] **Step 10.8: Type-check the frontend**

```bash
cd frontend && npm run build
```

Expected: build succeeds, no TS errors.

- [ ] **Step 10.9: Commit**

```bash
git add frontend/src/
git commit -m "feat(matching): swap match_rationale display for match_summary in UI"
```

---

## Task 11: End-to-end smoke + final verification

- [ ] **Step 11.1: Run the full unit + integration suite**

```bash
uv run pytest tests/unit/ tests/integration/ -v
```

Expected: all green. If anything fails that wasn't touched, root-cause first — do not paper over.

- [ ] **Step 11.2: Run the matching-specific smoke (if available)**

```bash
uv run pytest tests/smoke/ -v
```

Expected: all green (or skipped if `--has-seed-api` not passed; that's fine for local).

- [ ] **Step 11.3: Manually verify the cleaner output on real prod data**

```bash
PYTHONPATH=. uv run python scripts/compare_jd_cleaning.py --sample 10 --out tmp/jd_clean_after
```

Expected: the script runs, the markdownify column is non-empty for all 10 jobs, structure is preserved.

- [ ] **Step 11.4: Manual UI spot-check**

- Start local dev: `docker compose up -d db && make migrate ARGS="upgrade head" && uv run uvicorn app.main:app --reload --port 8000`
- In a separate shell: `cd frontend && npm run dev`
- Browser: open http://localhost:5173, log in (or use a seeded test profile), trigger a sync, open a matched job card.
- Confirm: 1-line summary visible, no rationale block, strengths and gaps lists shown.

- [ ] **Step 11.5: Confirm the spec's "no UI rationale" promise**

```bash
grep -rn "match_rationale" frontend/src/
```

Expected output: only the `client.ts` type definition and possibly the test fixture mention `match_rationale`. No JSX/TSX render block should reference it.

- [ ] **Step 11.6: Confirm DB-side audit trail still works**

After triggering a match in step 11.4, query:

```bash
docker exec job-application-agent-db-1 psql -U jobagent -d jobagent -c "SELECT match_score, left(match_summary, 50) AS summary, left(match_rationale, 50) AS rationale FROM applications WHERE match_score IS NOT NULL ORDER BY updated_at DESC LIMIT 5;"
```

Expected: both `summary` and `rationale` populated for recently scored rows.

- [ ] **Step 11.7: No commit needed for this task — verification only**

If everything passes, the branch is ready for PR.

---

## Post-merge operational steps (NOT part of this PR; record in PR description)

1. After deploy completes, run the backfill against prod from the Cloud Run job runner (or wherever one-off scripts run):

   ```bash
   uv run python scripts/backfill_job_description_clean.py --batch-size 200
   ```

   Expect ~3,800 rows processed in <2 minutes.

2. Watch `match.scored` structlog entries for one week. Anything to flag:
   - `rationale` field exceeding ~30 words (cap is 20; small overage is fine, large is not)
   - Spike in `match.rate_limit_skip` (would indicate the new prompt's input bloated despite design)

3. Follow-up PR (separate, low priority): rename `Job.description_md` → `Job.description_html`. Pure refactor — column rename + update all references. Not bundled in this PR to keep the migration scope minimal.

---

## Files-touched summary (for PR description)

**Backend**
- `app/services/html_cleaner.py` (new)
- `app/services/job_service.py` — populate `description_clean` in `upsert_job`
- `app/services/match_service.py` — `format_profile_text` always emits Locations; `JobContext` gains `location`/`workplace_type`; persist `match_summary`
- `app/agents/matching_agent.py` — split prompt, new tool args, `ScoreResult.summary`, `JobContext` location fields
- `app/agents/test_llm.py` — fake LLM response includes `summary`
- `app/api/applications.py` — serialize `match_summary` in both endpoints
- `app/models/job.py` — `description_clean: str | None`
- `app/models/application.py` — `match_summary: str | None`
- `alembic/versions/<ts>_add_job_description_clean.py` (new)
- `alembic/versions/<ts>_add_application_match_summary.py` (new)
- `scripts/backfill_job_description_clean.py` (new)

**Frontend**
- `frontend/src/api/client.ts` — `match_summary: string | null`
- `frontend/src/components/MatchCard.tsx` — display `match_summary`, drop rationale block
- `frontend/src/components/MatchCard.test.tsx` — fixture + new assertion
- `frontend/src/pages/ApplicationReview.tsx` — display `match_summary`

**Tests**
- `tests/unit/test_html_cleaner.py` (new)
- `tests/unit/test_matching_agent.py` (new)
- `tests/unit/test_match_service.py` — Locations rendering tests
- `tests/integration/test_job_sync.py` — `description_clean` populated
- `tests/integration/test_backfill_description_clean.py` (new)
- `tests/integration/test_match_scoring.py` — `match_summary` persisted
- `tests/integration/test_jobs_endpoint.py` (or wherever the API serialize test lives)
