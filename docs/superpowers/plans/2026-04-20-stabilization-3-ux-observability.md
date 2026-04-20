# Stabilization 3 — UX + Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six silent-failure surfaces: streaming error UX, resume extraction error differentiation, Adzuna enrichment observability, Sentry startup confirmation, structured-log timing, and cron workflow visibility.

**Architecture:** Six isolated, non-intersecting changes across backend services, the FastAPI router layer, GitHub Actions workflow, and the React frontend. Each task commits independently and passes tests before the next begins.

**Tech Stack:** FastAPI + SQLModel + LangGraph + structlog (backend); React 18 + TypeScript + Vitest + MSW v2 (frontend); pytest + httpx + testcontainers (tests); `google.api_core.exceptions.ResourceExhausted` for LLM quota errors.

---

## File Map

| File | Change |
|------|--------|
| `app/services/resume_extraction.py` | Add error classes; typed raises; entry/exit timing log |
| `app/services/profile_service.py` | `save_resume` returns `(profile, extraction_status)` |
| `app/api/profile.py` | Unpack tuple; add `extraction_status` to response |
| `app/sources/adzuna_enrichment.py` | Per-call structured logs |
| `app/services/job_sync_service.py` | Enrichment summary counters |
| `app/services/match_service.py` | Add `duration_ms` to existing complete log; add started log |
| `app/services/application_service.py` | Add `duration_ms` to generate_materials done/failed logs |
| `app/scheduler/tasks.py` | Return summary dicts from all three task functions |
| `app/api/internal_cron.py` | Wire task return values + timing into response JSON |
| `app/main.py` | Sentry init wrapped in try/except with structured logs |
| `.github/workflows/cron.yml` | Replace `curl -sf` with response-capturing pattern |
| `frontend/src/api/client.ts` | `res.ok` check + `onError` callback in `sendMessage`; `res.ok` check in `uploadResume` |
| `frontend/src/pages/Onboarding.tsx` | try/catch wrappers; error message rendering; extraction banner |
| `tests/unit/test_resume_extraction_errors.py` | New: 4 unit tests for typed errors |
| `tests/integration/test_cron_endpoints.py` | New: 4 integration tests for cron response shape |
| `frontend/src/pages/Onboarding.test.tsx` | New: 2 frontend tests for error states |

---

## Task 1: Resume extraction error types (TDD)

**Files:**
- Create: `tests/unit/test_resume_extraction_errors.py`
- Modify: `app/services/resume_extraction.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_resume_extraction_errors.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from google.api_core.exceptions import ResourceExhausted

from app.agents.llm_safe import BudgetExhausted
from app.services.resume_extraction import (
    extract_profile_from_resume,
    InvalidResumeError,
    LLMUnavailableError,
)


def _settings():
    s = MagicMock()
    s.environment = "test"
    return s


@pytest.mark.asyncio
async def test_resource_exhausted_raises_llm_unavailable():
    with patch("app.services.resume_extraction.get_settings", return_value=_settings()), \
         patch("app.services.resume_extraction.safe_ainvoke", side_effect=ResourceExhausted("quota")):
        with pytest.raises(LLMUnavailableError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_budget_exhausted_raises_llm_unavailable():
    with patch("app.services.resume_extraction.get_settings", return_value=_settings()), \
         patch("app.services.resume_extraction.safe_ainvoke", side_effect=BudgetExhausted("budget")):
        with pytest.raises(LLMUnavailableError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_invalid_json_raises_invalid_resume():
    mock_resp = MagicMock()
    mock_resp.content = "not json {{"
    with patch("app.services.resume_extraction.get_settings", return_value=_settings()), \
         patch("app.services.resume_extraction.safe_ainvoke", return_value=mock_resp):
        with pytest.raises(InvalidResumeError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_valid_json_returns_dict():
    mock_resp = MagicMock()
    mock_resp.content = '{"full_name": "Jane Doe", "skills": []}'
    with patch("app.services.resume_extraction.get_settings", return_value=_settings()), \
         patch("app.services.resume_extraction.safe_ainvoke", return_value=mock_resp):
        result = await extract_profile_from_resume("resume text")
        assert result["full_name"] == "Jane Doe"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_resume_extraction_errors.py -v
```

Expected: `ImportError: cannot import name 'InvalidResumeError' from 'app.services.resume_extraction'`

- [ ] **Step 3: Implement error classes and typed raises in `app/services/resume_extraction.py`**

Replace the entire file with:

```python
"""
LLM-based resume extraction.

Extracts structured profile data from resume markdown text using Gemini Flash.
Called by profile_service.save_resume() after storing raw text.
"""

import json
import re
import time

import structlog
from google.api_core.exceptions import ResourceExhausted
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agents.llm_safe import BudgetExhausted, safe_ainvoke
from app.config import get_settings

log = structlog.get_logger()

EXTRACTION_PROMPT = """\
Extract structured profile data from this resume. Return ONLY valid JSON with these fields \
(omit any field you cannot confidently extract, do not guess):

- full_name: string
- email: string
- phone: string
- linkedin_url: string
- github_url: string
- portfolio_url: string
- target_roles: list of 1-3 appropriate job title strings inferred from experience
- skills: list of objects, each with:
    name (string), category (one of: language, framework, cloud, domain, tool),
    proficiency (one of: expert, proficient, familiar), years (number or null)
- work_experiences: list of objects, each with:
    company (string), title (string), start_date (YYYY-MM-DD string),
    end_date (YYYY-MM-DD string or null for current), description_md (1-2 sentence summary),
    technologies (list of strings)

Return only the JSON object, no markdown fences.

Resume:
{resume_md}"""


class ResumeExtractionError(Exception):
    pass


class LLMUnavailableError(ResumeExtractionError):
    pass


class InvalidResumeError(ResumeExtractionError):
    pass


async def extract_profile_from_resume(resume_md: str) -> dict:
    """
    Use Gemini Flash to extract structured profile data from resume text.

    Returns a dict with keys: full_name, email, phone, linkedin_url, github_url,
    portfolio_url, target_roles, skills (list), work_experiences (list).

    Raises:
        LLMUnavailableError: quota exhausted or budget exceeded
        InvalidResumeError: LLM response was not parseable JSON
        ResumeExtractionError: any other extraction failure
    """
    settings = get_settings()
    t0 = time.perf_counter()

    try:
        if settings.environment == "test":
            from app.agents.test_llm import get_fake_llm
            llm = get_fake_llm("resume_extraction")
        else:
            llm = ChatGoogleGenerativeAI(
                model=settings.llm_resume_extraction_model,
                google_api_key=settings.google_api_key.get_secret_value(),
            )
        prompt = EXTRACTION_PROMPT.format(resume_md=resume_md[:8000])
        response = await safe_ainvoke(llm, prompt)
        raw = response.content if isinstance(response.content, str) else str(response.content)

        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise InvalidResumeError("LLM returned non-dict JSON")

        await log.ainfo(
            "resume_extraction.completed",
            fields=len(data),
            resume_length=len(resume_md),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
        return data

    except (ResourceExhausted, BudgetExhausted) as exc:
        await log.awarning("resume_extraction.llm_unavailable", error=str(exc))
        raise LLMUnavailableError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        await log.awarning("resume_extraction.parse_failed", error=str(exc))
        raise InvalidResumeError(str(exc)) from exc
    except ResumeExtractionError:
        raise
    except Exception as exc:
        await log.awarning("resume_extraction.failed", error=str(exc))
        raise ResumeExtractionError(str(exc)) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_resume_extraction_errors.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_resume_extraction_errors.py app/services/resume_extraction.py
git commit -m "feat: add typed resume extraction errors; entry/exit timing log"
```

---

## Task 2: Wire extraction_status through profile_service and API

**Files:**
- Modify: `app/services/profile_service.py`
- Modify: `app/api/profile.py`

- [ ] **Step 1: Update `app/services/profile_service.py`**

The change is to `save_resume`. Add the necessary imports at the top of the imports section:

```python
from app.services.resume_extraction import (
    InvalidResumeError,
    LLMUnavailableError,
    ResumeExtractionError,
    extract_profile_from_resume,
)
```

Remove the existing import of `extract_profile_from_resume` (it was: `from app.services.resume_extraction import extract_profile_from_resume`). Replace the entire `save_resume` function:

```python
async def save_resume(
    profile_id: uuid.UUID, filename: str, raw_bytes: bytes, session: AsyncSession
) -> tuple[UserProfile, str]:
    """
    Parse and store a resume file, then run LLM extraction.

    Returns (profile, extraction_status) where extraction_status is one of:
      "ok"         — extraction succeeded and was applied
      "llm_error"  — LLM quota exhausted or temporarily unavailable
      "parse_error" — LLM response was not parseable or resume was unstructured
      "skipped"    — no markdown text could be extracted from the file
    """
    profile = await session.get(UserProfile, profile_id)
    md = parse_resume(filename, raw_bytes)
    profile.base_resume_raw = raw_bytes
    profile.base_resume_md = md
    profile.updated_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    extraction_status = "skipped"
    if md:
        try:
            extracted = await extract_profile_from_resume(md)
            if extracted:
                await _apply_extracted_resume_data(profile_id, extracted, session)
                await session.refresh(profile)
            extraction_status = "ok"
        except LLMUnavailableError:
            extraction_status = "llm_error"
        except (InvalidResumeError, ResumeExtractionError):
            extraction_status = "parse_error"

    return profile, extraction_status
```

- [ ] **Step 2: Update `app/api/profile.py` upload endpoint**

Find the `upload_resume` handler. The current line 132 is:
```python
updated = await profile_service.save_resume(profile.id, file.filename or "resume", raw, session)
return {
    "id": str(updated.id),
    "base_resume_md": updated.base_resume_md,
    "message": "Resume uploaded and parsed successfully.",
}
```

Replace those lines with:
```python
updated, extraction_status = await profile_service.save_resume(
    profile.id, file.filename or "resume", raw, session
)
return {
    "id": str(updated.id),
    "base_resume_md": updated.base_resume_md,
    "extraction_status": extraction_status,
    "message": "Resume uploaded successfully.",
}
```

- [ ] **Step 3: Run unit tests to verify nothing broke**

```bash
uv run pytest tests/unit/ -v -k "not test_resume_extraction_errors"
```

Expected: All existing unit tests PASSED

- [ ] **Step 4: Run the new extraction error tests**

```bash
uv run pytest tests/unit/test_resume_extraction_errors.py -v
```

Expected: 4 PASSED (still green after profile_service changes)

- [ ] **Step 5: Commit**

```bash
git add app/services/profile_service.py app/api/profile.py
git commit -m "feat: surface extraction_status in resume upload response"
```

---

## Task 3: Adzuna enrichment observability

**Files:**
- Modify: `app/sources/adzuna_enrichment.py`
- Modify: `app/services/job_sync_service.py`

- [ ] **Step 1: Add per-call logs to `app/sources/adzuna_enrichment.py`**

Replace the `fetch_full_description` function (lines 16-51) with:

```python
async def fetch_full_description(
    redirect_url: str,
) -> tuple[str | None, dict | None, str | None]:
    """
    Fetch full job description and card metadata from an Adzuna redirect URL.

    Follows redirects and captures the final destination URL.
    When the final URL is not on adzuna.com, Adzuna-specific CSS selectors are skipped
    and only trafilatura is used for description extraction.

    Returns:
        (description_text, card_info, resolved_url) where:
        - description_text: extracted main text or None
        - card_info: dict with salary/contract_type keys (Adzuna pages only) or None
        - resolved_url: the final URL after following redirects, or None on failure
    Returns (None, None, None) on any fetch failure.
    """
    await log.ainfo("adzuna.enrichment.attempt", url=redirect_url)
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-agent/1.0)"},
        ) as client:
            response = await client.get(redirect_url)
            response.raise_for_status()
            html = response.text
            resolved_url = str(response.url)
    except Exception as exc:
        await log.awarning("adzuna.enrichment.failed", url=redirect_url, error=str(exc))
        return None, None, None

    is_adzuna_page = "adzuna.com" in resolved_url.lower()
    description = _extract_body(html, use_adzuna_selector=is_adzuna_page)
    card_info = _extract_card_info(html) if is_adzuna_page else None
    salary_found = bool(card_info and card_info.get("salary"))
    await log.ainfo("adzuna.enrichment.success", url=redirect_url, salary=salary_found)
    return description, card_info, resolved_url
```

- [ ] **Step 2: Add enrichment summary counters in `app/services/job_sync_service.py`**

Replace the `_enrich_jobs` function (lines 115-151) with:

```python
async def _enrich_jobs(
    jobs: list[JobData],
    existing_full: set[str],
) -> list[JobData]:
    """
    Fetch full descriptions from Adzuna detail pages.

    Skips jobs whose external_id is already in existing_full (already enriched in DB).
    Caps concurrency at 5. Individual failures keep the truncated API description.
    """
    sem = asyncio.Semaphore(5)
    enriched_count = 0
    salary_count = 0
    failed_count = 0

    async def enrich_one(j: JobData) -> JobData:
        nonlocal enriched_count, salary_count, failed_count
        if j.external_id in existing_full:
            return j
        async with sem:
            try:
                desc, meta, resolved_url = await fetch_full_description(j.apply_url)
                if desc:
                    j.description_md = desc
                    enriched_count += 1
                if meta:
                    j.salary = meta.get("salary")
                    j.contract_type = meta.get("contract_type")
                    if j.salary:
                        salary_count += 1
                if resolved_url and resolved_url != j.apply_url:
                    j.apply_url = resolved_url
                    j.ats_type = detect_ats_type(resolved_url)
                    j.supports_api_apply = supports_api_apply(resolved_url)
            except Exception as exc:
                failed_count += 1
                await log.awarning(
                    "job_sync.enrich_failed",
                    external_id=j.external_id,
                    error=str(exc),
                )
        return j

    result = list(await asyncio.gather(*[enrich_one(j) for j in jobs]))
    await log.ainfo(
        "adzuna.sync.summary",
        total=len(jobs),
        enriched=enriched_count,
        salary_parsed=salary_count,
        failed=failed_count,
    )
    return result
```

- [ ] **Step 3: Run lint**

```bash
uv run ruff check app/sources/adzuna_enrichment.py app/services/job_sync_service.py
```

Expected: no output (no issues)

- [ ] **Step 4: Commit**

```bash
git add app/sources/adzuna_enrichment.py app/services/job_sync_service.py
git commit -m "feat: add per-call and summary structured logs for Adzuna enrichment"
```

---

## Task 4: Sentry startup confirmation

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Replace Sentry init block in `app/main.py`**

Find the current block (lines 58-63):
```python
    # Init Sentry
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            traces_sample_rate=0.1,
            environment=settings.environment,
        )
```

Replace with:
```python
    # Init Sentry — log confirmation so operators can verify it's active in production
    if settings.sentry_dsn:
        try:
            dsn_val = settings.sentry_dsn.get_secret_value()
            sentry_sdk.init(
                dsn=dsn_val,
                traces_sample_rate=0.1,
                environment=settings.environment,
            )
            await log.ainfo("sentry.enabled", dsn_suffix=dsn_val[-4:])
        except Exception as exc:
            await log.awarning("sentry.init_failed", error=str(exc))
    else:
        await log.ainfo("sentry.disabled", reason="no_dsn_configured")
```

- [ ] **Step 2: Run lint**

```bash
uv run ruff check app/main.py
```

Expected: no output

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: log sentry.enabled/disabled/init_failed on startup"
```

---

## Task 5: Structured-log timing on match and generation

**Files:**
- Modify: `app/services/match_service.py`
- Modify: `app/services/application_service.py`

- [ ] **Step 1: Add timing to `app/services/match_service.py`**

Add `import time` to the top-level imports (after `import uuid`).

At the start of `score_and_match` (line 83), add:
```python
    t0 = time.perf_counter()
    await log.ainfo("match.score_and_match.started", profile_id=str(profile.id))
```

Find the existing `"match.complete"` log near line 193:
```python
    await log.ainfo(
        "match.complete",
        profile_id=str(profile.id),
        scored=len(scored_apps),
        total_jobs=len(job_contexts),
    )
```

Add `duration_ms` to it:
```python
    await log.ainfo(
        "match.complete",
        profile_id=str(profile.id),
        scored=len(scored_apps),
        total_jobs=len(job_contexts),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
```

- [ ] **Step 2: Add timing to `app/services/application_service.py`**

Add `import time` to the top-level imports (look for `from datetime import UTC, datetime` — add `import time` nearby).

At the very start of the `generate_materials` function body (line 67, right after `async def generate_materials(...) -> None:`), add:
```python
    t0 = time.perf_counter()
    await log.ainfo("generation.started", application_id=str(application_id))
```

Find the existing `"generate_materials.done"` log (line 143):
```python
    await log.ainfo("generate_materials.done", application_id=str(application_id))
```

Replace with:
```python
    await log.ainfo(
        "generation.completed",
        application_id=str(application_id),
        status=app.generation_status,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
```

Find the `"generate_materials.failed"` log (line 127):
```python
        await log.aexception(
            "generate_materials.failed",
            application_id=str(application_id),
            error=str(exc),
        )
```

Replace with:
```python
        await log.aexception(
            "generation.failed",
            application_id=str(application_id),
            error=str(exc),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
```

- [ ] **Step 3: Run lint**

```bash
uv run ruff check app/services/match_service.py app/services/application_service.py
```

Expected: no output

- [ ] **Step 4: Run unit tests**

```bash
uv run pytest tests/unit/ -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/match_service.py app/services/application_service.py
git commit -m "feat: add entry/exit timing logs to match and generation services"
```

---

## Task 6: Cron tasks return summaries + integration tests (TDD)

**Files:**
- Create: `tests/integration/test_cron_endpoints.py`
- Modify: `app/scheduler/tasks.py`
- Modify: `app/api/internal_cron.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_cron_endpoints.py`:

```python
"""
Integration tests for /internal/cron/* endpoints.

These tests verify:
- Each endpoint returns a structured JSON summary (not just {"status": "ok"})
- The summary contains at minimum a status key and a numeric count key
- Invalid secrets are rejected with 403
"""
import pytest
from httpx import AsyncClient


@pytest.fixture
async def client(patch_settings):
    from app.main import app

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


CRON_SECRET = "dev-cron-secret"  # matches default SecretStr("dev-cron-secret") in config.py


@pytest.mark.asyncio
async def test_cron_sync_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["profiles_synced"], int)
    assert isinstance(data["duration_ms"], int)


@pytest.mark.asyncio
async def test_cron_generation_queue_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/generation-queue",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["attempted"], int)
    assert isinstance(data["duration_ms"], int)


@pytest.mark.asyncio
async def test_cron_maintenance_returns_structured_summary(client):
    resp = await client.post(
        "/internal/cron/maintenance",
        headers={"X-Cron-Secret": CRON_SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["stale_jobs"], int)
    assert isinstance(data["duration_ms"], int)


@pytest.mark.asyncio
async def test_cron_rejects_invalid_secret(client):
    resp = await client.post(
        "/internal/cron/sync",
        headers={"X-Cron-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_cron_endpoints.py -v
```

Expected: `AssertionError` on `data["profiles_synced"]` — key not found since current response is `{"status": "ok"}`

- [ ] **Step 3: Update `app/scheduler/tasks.py` to return summary dicts**

Add `import time` after the existing imports.

Replace `run_job_sync` (currently returns `None`):

```python
async def run_job_sync() -> dict:
    """Sync jobs for all users with active search. Returns a summary dict."""
    import time

    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services import job_sync_service, match_service

    t0 = time.perf_counter()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.search_active.is_(True))
        )
        profiles = result.scalars().all()

    profiles_synced = 0
    total_new = 0
    total_updated = 0
    total_stale = 0

    for profile in profiles:
        try:
            async with factory() as session:
                sync_result = await job_sync_service.sync_profile(profile, session)
                profiles_synced += 1
                total_new += sync_result.get("new_jobs", 0)
                total_updated += sync_result.get("updated_jobs", 0)
                total_stale += sync_result.get("stale_jobs", 0)
                await match_service.score_and_match(profile, session)
        except Exception as exc:
            await log.aexception("scheduler.sync_error", profile_id=str(profile.id), error=str(exc))
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass

    return {
        "profiles_synced": profiles_synced,
        "total_new_jobs": total_new,
        "total_updated_jobs": total_updated,
        "total_stale_jobs": total_stale,
    }
```

Replace `run_generation_queue` (currently returns `None`):

```python
async def run_generation_queue() -> dict:
    """Generate materials for applications stuck in pending/generating status. Returns a summary dict."""
    import time

    from app.database import get_session_factory
    from app.models.application import Application
    from app.services.application_service import generate_materials

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Application)
            .where(
                Application.generation_status.in_(["pending"]),
                Application.generation_attempts < 3,
            )
            .limit(10)
        )
        apps = result.scalars().all()
        app_ids = [a.id for a in apps]

    attempted = len(app_ids)
    succeeded = 0
    failed = 0

    for app_id in app_ids:
        try:
            async with factory() as session:
                await generate_materials(app_id, session)
                succeeded += 1
        except Exception as exc:
            failed += 1
            await log.aexception(
                "scheduler.generation_error", app_id=str(app_id), error=str(exc)
            )

    return {"attempted": attempted, "succeeded": succeeded, "failed": failed}
```

Replace `run_daily_maintenance` (currently returns `None`) — add return statement at the end:

After the existing `trimmed = trim_result.rowcount` line, add at the very end of the function (after `await session.commit()`):

```python
        return {
            "stale_jobs": stale,
            "searches_paused": len(expired_profiles),
            "applications_trimmed": trimmed,
        }
```

The full replacement for `run_daily_maintenance`:

```python
async def run_daily_maintenance() -> dict:
    """Mark stale jobs + auto-pause expired searches + trim excess matched applications."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.user_profile import UserProfile
    from app.services.job_service import mark_stale_jobs

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        stale = await mark_stale_jobs(settings.job_stale_after_days, session)
        await log.ainfo("maintenance.stale_jobs", count=stale)

        result = await session.execute(
            select(UserProfile).where(
                UserProfile.search_active.is_(True),
                UserProfile.search_expires_at.is_not(None),
                UserProfile.search_expires_at < datetime.now(UTC),
            )
        )
        expired_profiles = result.scalars().all()
        for profile in expired_profiles:
            profile.search_active = False
            profile.updated_at = datetime.now(UTC)
            session.add(profile)
            await log.awarning("maintenance.search_paused", profile_id=str(profile.id))
        if expired_profiles:
            await session.commit()
            await log.ainfo("maintenance.searches_paused", count=len(expired_profiles))

        trim_result = await session.execute(
            text("""
                DELETE FROM applications
                WHERE status = 'matched'
                  AND id NOT IN (
                    SELECT id FROM applications a2
                    WHERE a2.profile_id = applications.profile_id
                      AND a2.status = 'matched'
                    ORDER BY a2.created_at DESC
                    LIMIT 500
                  )
            """)
        )
        await session.commit()
        trimmed = trim_result.rowcount
        if trimmed:
            await log.ainfo("maintenance.applications_trimmed", count=trimmed)

    return {
        "stale_jobs": stale,
        "searches_paused": len(expired_profiles),
        "applications_trimmed": trimmed,
    }
```

- [ ] **Step 4: Update `app/api/internal_cron.py` to wire summary into response**

Replace the entire file:

```python
import time

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from app.agents.llm_safe import BudgetExhausted
from app.config import Settings, get_settings
from app.scheduler.tasks import run_daily_maintenance, run_generation_queue, run_job_sync

log = structlog.get_logger()
router = APIRouter(prefix="/internal/cron", tags=["cron"])


def get_cron_settings() -> Settings:
    return get_settings()


async def verify_secret(
    x_cron_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_cron_settings),
) -> None:
    expected = settings.cron_shared_secret.get_secret_value()
    if x_cron_secret is None or x_cron_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.post("/sync", dependencies=[Depends(verify_secret)])
async def cron_sync():
    t0 = time.perf_counter()
    await log.ainfo("cron.sync.started")
    result = {}
    try:
        result = await run_job_sync()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.sync.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue():
    t0 = time.perf_counter()
    await log.ainfo("cron.generation_queue.started")
    result = {}
    try:
        result = await run_generation_queue()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.generation_queue.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    t0 = time.perf_counter()
    await log.ainfo("cron.maintenance.started")
    result = {}
    try:
        result = await run_daily_maintenance()
    except BudgetExhausted:
        pass
    duration_ms = int((time.perf_counter() - t0) * 1000)
    await log.ainfo("cron.maintenance.completed", duration_ms=duration_ms, **result)
    return {"status": "ok", "duration_ms": duration_ms, **result}
```

- [ ] **Step 5: Run integration tests to verify they pass**

```bash
uv run pytest tests/integration/test_cron_endpoints.py -v
```

Expected: 4 PASSED (requires Docker for testcontainers)

- [ ] **Step 6: Run full unit test suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_cron_endpoints.py app/scheduler/tasks.py app/api/internal_cron.py
git commit -m "feat: cron tasks return structured summaries; add timing and entry/exit logs to cron handlers"
```

---

## Task 7: Cron workflow response visibility

**Files:**
- Modify: `.github/workflows/cron.yml`

- [ ] **Step 1: Replace `curl -sf` pattern in all three jobs**

Replace the entire `.github/workflows/cron.yml` file:

```yaml
name: Cron

on:
  schedule:
    - cron: '0 */4 * * *'
    - cron: '*/10 * * * *'
    - cron: '0 3 * * *'
  workflow_dispatch:

jobs:
  sync:
    if: github.event.schedule == '0 */4 * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger job sync
        env:
          CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
          CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
        run: |
          response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
            "$CLOUD_RUN_URL/internal/cron/sync")
          body=$(echo "$response" | head -n -1)
          code=$(echo "$response" | tail -n 1)
          echo "HTTP $code"
          echo "$body"
          [ "$code" -lt 400 ] || exit 1

  generation-queue:
    if: github.event.schedule == '*/10 * * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger generation queue
        env:
          CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
          CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
        run: |
          response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
            "$CLOUD_RUN_URL/internal/cron/generation-queue")
          body=$(echo "$response" | head -n -1)
          code=$(echo "$response" | tail -n 1)
          echo "HTTP $code"
          echo "$body"
          [ "$code" -lt 400 ] || exit 1

  maintenance:
    if: github.event.schedule == '0 3 * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger daily maintenance
        env:
          CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
          CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
        run: |
          response=$(curl -s -w "\n%{http_code}" -X POST \
            -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
            "$CLOUD_RUN_URL/internal/cron/maintenance")
          body=$(echo "$response" | head -n -1)
          code=$(echo "$response" | tail -n 1)
          echo "HTTP $code"
          echo "$body"
          [ "$code" -lt 400 ] || exit 1
```

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/cron.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/cron.yml
git commit -m "fix: echo cron response body and HTTP status code before failing on error"
```

---

## Task 8: Frontend client.ts error handling

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Update `sendMessage` and `uploadResume` in `frontend/src/api/client.ts`**

Replace the `sendMessage` property (lines 169-196) with:

```typescript
  sendMessage: (message: string, onChunk: (text: string) => void, onError?: (err: Error) => void): Promise<void> => {
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    return fetch('/api/chat/messages', {
      method: 'POST',
      headers,
      body: JSON.stringify({ message }),
    }).then(async (res) => {
      if (!res.ok) {
        const text = await res.text()
        const err = new Error(`${res.status}: ${text}`)
        if (onError) { onError(err); return }
        throw err
      }
      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value)
        for (const line of text.split('\n')) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') return
            try {
              const parsed = JSON.parse(data)
              if (parsed.content) onChunk(parsed.content)
            } catch {
              const err = new Error(`stream parse error: ${data}`)
              if (onError) { onError(err); return }
            }
          }
        }
      }
    })
  },
```

Replace the `uploadResume` property (lines 111-115) with:

```typescript
  uploadResume: async (file: File): Promise<{ id: string; base_resume_md: string | null; extraction_status: string; message: string }> => {
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const form = new FormData()
    form.append('file', file)
    const r = await fetch('/api/profile/upload', { method: 'POST', body: form, headers })
    if (!r.ok) {
      const text = await r.text()
      throw new Error(`${r.status}: ${text}`)
    }
    return r.json()
  },
```

- [ ] **Step 2: Type-check the frontend**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: add res.ok checks and onError callback to client.ts"
```

---

## Task 9: Onboarding.tsx error UI + frontend tests (TDD)

**Files:**
- Create: `frontend/src/pages/Onboarding.test.tsx`
- Modify: `frontend/src/pages/Onboarding.tsx`

- [ ] **Step 1: Write the failing frontend tests**

Create `frontend/src/pages/Onboarding.test.tsx`:

```typescript
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import Onboarding from './Onboarding'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Onboarding', () => {
  it('shows error message and re-enables Send when chat request fails', async () => {
    server.use(
      http.post('/api/chat/messages', () =>
        HttpResponse.json({ detail: 'Server Error' }, { status: 500 })
      )
    )
    render(<Onboarding />, { wrapper })

    const input = screen.getByPlaceholderText('Type your preferences...')
    const sendBtn = screen.getByRole('button', { name: 'Send' })

    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(screen.getByText('Something went wrong — please try again.')).toBeInTheDocument()
    })
    expect(sendBtn).not.toBeDisabled()
  })

  it('shows extraction error banner when resume upload returns parse_error', async () => {
    server.use(
      http.post('/api/profile/upload', () =>
        HttpResponse.json({
          id: '00000000-0000-0000-0000-000000000001',
          base_resume_md: null,
          extraction_status: 'parse_error',
          message: 'Resume uploaded successfully.',
        })
      ),
      http.post('/api/chat/messages', () =>
        new HttpResponse('data: [DONE]\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      )
    )
    render(<Onboarding />, { wrapper })

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['pdf content'], 'resume.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(
        screen.getByText(/couldn't read the structure/)
      ).toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npm test src/pages/Onboarding.test.tsx -- --run
```

Expected: both tests FAIL (component has no error state or extraction banner yet)

- [ ] **Step 3: Update `frontend/src/pages/Onboarding.tsx`**

**3a.** Add `error?: boolean` to the `Message` interface:

```typescript
interface Message {
  role: 'user' | 'assistant'
  content: string
  error?: boolean
}
```

**3b.** Add `uploadError` state below the existing `useQuery` call:

```typescript
const [uploadError, setUploadError] = useState<string | null>(null)
```

**3c.** Replace the `sendMessage` function (lines 156-178) with:

```typescript
  const sendMessage = async () => {
    if (!input.trim() || sending) return
    const userMsg = input.trim()
    setInput('')
    setSending(true)

    setMessages((prev) => [...prev, { role: 'user', content: userMsg }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])

    try {
      await api.sendMessage(
        userMsg,
        (chunk) => {
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: updated[updated.length - 1].content + chunk,
            }
            return updated
          })
        },
        (err) => {
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              role: 'assistant',
              content: 'Something went wrong — please try again.',
              error: true,
            }
            return updated
          })
          setSending(false)
        }
      )
    } catch {
      setMessages((prev) => {
        const updated = [...prev]
        updated[updated.length - 1] = {
          role: 'assistant',
          content: 'Something went wrong — please try again.',
          error: true,
        }
        return updated
      })
    }
    setSending(false)
    refetchProfile()
  }
```

**3d.** Replace the `handleUpload` function (lines 180-209) with:

```typescript
  const handleUpload = async (file: File) => {
    setUploading(true)
    setUploadError(null)
    try {
      const result = await api.uploadResume(file)
      if (result.extraction_status === 'llm_error') {
        setUploadError(
          "Resume uploaded, but we couldn't extract your profile right now — the AI is temporarily unavailable. Try editing your profile manually."
        )
      } else if (result.extraction_status === 'parse_error') {
        setUploadError(
          "Resume uploaded, but we couldn't read the structure — try a plain-text or clearly formatted PDF."
        )
      }
      refetchProfile()
      const userMsg = "I've uploaded my resume. Please review it and help me complete my profile."
      setInput('')
      setSending(true)
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: userMsg },
        { role: 'assistant', content: '' },
      ])
      await api.sendMessage(
        userMsg,
        (chunk) => {
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: updated[updated.length - 1].content + chunk,
            }
            return updated
          })
        },
        (err) => {
          setMessages((prev) => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              role: 'assistant',
              content: 'Something went wrong — please try again.',
              error: true,
            }
            return updated
          })
          setSending(false)
        }
      )
      setSending(false)
      refetchProfile()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed — please try again.')
    } finally {
      setUploading(false)
    }
  }
```

**3e.** Update the message bubble rendering (find the `className` block for the assistant messages, around line 235) to add error styling:

```tsx
              className={`max-w-[85%] px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white rounded-br-sm'
                  : msg.error
                  ? 'bg-red-50 text-red-700 rounded-bl-sm'
                  : 'bg-gray-100 text-gray-800 rounded-bl-sm'
              }`}
```

**3f.** Add the upload error banner in the Input area section, right after the file `<input>` element (before the Resume button):

```tsx
          {uploadError && (
            <p className="mt-1 text-xs text-red-600">{uploadError}</p>
          )}
```

Place this banner just below the `<div className="flex gap-2">` line (the flex container for Resume button, input, and Send button), as a sibling `<div>` wrapping the flex container. Specifically, wrap the flex container and add the error below:

```tsx
      <div className="border-t pt-3">
        {uploadError && (
          <p className="mb-2 text-xs text-red-600">{uploadError}</p>
        )}
        <div className="flex gap-2">
          ... (existing Resume button, input, Send button) ...
        </div>
      </div>
```

- [ ] **Step 4: Run frontend tests to verify they pass**

```bash
cd frontend && npm test src/pages/Onboarding.test.tsx -- --run
```

Expected: both tests PASSED

- [ ] **Step 5: Type-check**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Onboarding.tsx frontend/src/pages/Onboarding.test.tsx
git commit -m "feat: add error UI to Onboarding chat and resume upload"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run all unit tests**

```bash
uv run pytest tests/unit/ -v
```

Expected: all PASSED (including the 4 new extraction error tests)

- [ ] **Step 2: Run integration tests**

```bash
uv run pytest tests/integration/ -v
```

Expected: all PASSED (requires Docker — testcontainers spins up Postgres automatically)

- [ ] **Step 3: Run coverage check**

```bash
uv run pytest tests/unit/ tests/integration/ --cov=app --cov-report=term --cov-fail-under=56
```

Expected: coverage ≥56% (the new tests raise coverage; this should still pass)

- [ ] **Step 4: Run all frontend tests**

```bash
cd frontend && npm test -- --run
```

Expected: all PASSED (including BudgetBanner, MatchCard, AuthContext, client, and the new Onboarding tests)

- [ ] **Step 5: Frontend coverage check**

```bash
cd frontend && npm test -- --run --coverage
```

Expected: lines threshold ≥29% met

- [ ] **Step 6: Lint check**

```bash
uv run ruff check app/ tests/
```

Expected: no output

- [ ] **Step 7: Push branch and open PR**

```bash
git push -u origin stabilization/ux-observability
gh pr create --title "feat: Spec 3 — UX + observability fixes" --body "$(cat <<'EOF'
## Summary

- Resume extraction now raises typed errors (`LLMUnavailableError`, `InvalidResumeError`) instead of silently returning `{}`
- `/api/profile/upload` response includes `extraction_status` so the frontend can show a targeted message
- Onboarding chat and upload flows catch errors and render error messages; Send button re-enables on failure
- Adzuna enrichment emits per-call and summary structured logs
- Sentry init is wrapped in try/except and logs `sentry.enabled/disabled/init_failed` on startup
- `score_and_match` and `generate_materials` emit entry + exit logs with `duration_ms`
- Cron tasks return structured summary dicts; `/internal/cron/*` endpoints include them in the JSON response
- `cron.yml` replaces `curl -sf` with a response-capturing pattern that echoes HTTP status + body before failing

## Test plan

- [ ] `uv run pytest tests/unit/ tests/integration/ -v` — all green
- [ ] `cd frontend && npm test -- --run` — all green
- [ ] Manual: disable network in browser devtools mid-chat → error message appears, Send re-enables
- [ ] Manual: upload a corrupted PDF → "couldn't read the structure" banner appears
- [ ] Manual: check startup logs on Cloud Run → confirm `sentry.enabled` or `sentry.disabled`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

**Spec coverage check:**
- Problem 1 (streaming error UX): Task 8 (client.ts) + Task 9 (Onboarding.tsx) ✓
- Problem 2 (resume extraction differentiation): Task 1 (error types) + Task 2 (wire through) ✓
- Problem 3 (Adzuna observability): Task 3 ✓
- Problem 4 (Sentry confirmation): Task 4 ✓
- Problem 5 (structured-log timing): Task 5 (match + generation) + Task 6 (cron handlers) ✓
- Problem 6 (cron visibility): Task 6 (summaries) + Task 7 (cron.yml) ✓
- Integration test for cron: Task 6 ✓
- Unit tests for extraction errors: Task 1 ✓
- Frontend tests for Onboarding: Task 9 ✓

**Type consistency:**
- `save_resume` returns `tuple[UserProfile, str]` in Task 2 — caller in `api/profile.py` unpacks as `updated, extraction_status` in same task ✓
- `sendMessage(message, onChunk, onError?)` defined in Task 8 — called with 3 args in Task 9 ✓
- `run_job_sync() -> dict` defined in Task 6 — `cron_sync()` calls `result = await run_job_sync()` in same task ✓
- `LLMUnavailableError`, `InvalidResumeError` defined in Task 1 — imported in Task 2 ✓

**Placeholder scan:** No TBDs or "similar to Task N" shortcuts found.
