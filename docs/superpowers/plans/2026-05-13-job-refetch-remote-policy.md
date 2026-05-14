# Job Re-Fetch Remote Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-safe full reset/re-fetch path and enforce remote-only trust rules so required office attendance cannot surface as a good remote match.

**Architecture:** Extend the existing wipe script into an atomic full reset utility that clears user-owned/job-search state, resets non-invalid slug freshness, and preserves company catalog rows. Add a small deterministic remote-policy guard used by both synchronous matching and worker matching so the LLM prompt is explicit, but the threshold guarantee is enforced in code. Keep raw job descriptions untruncated in storage and apply the 20k cap only at LLM prompt construction.

**Tech Stack:** FastAPI app with SQLModel/SQLAlchemy async sessions, Postgres, pytest unit/integration tests, existing LangGraph/Gemini matching agent.

---

## File Map

- Modify `scripts/wipe_job_data.py`: replace job-only wipe with an atomic full reset mode that wipes users/profiles/jobs/applications/documents/queues/checkpoints, preserves companies and slug rows, and resets non-invalid slug freshness.
- Create `tests/integration/test_wipe_job_data.py`: database-backed coverage for wipe scope, slug freshness reset, invalid slug preservation, checkpoint cleanup, and rollback-on-error.
- Create `app/services/remote_policy.py`: deterministic runtime guard for required office attendance versus remote-only or target-location profiles.
- Create `tests/unit/test_remote_policy.py`: direct policy tests without LLM calls.
- Modify `app/agents/matching_agent.py`: strengthen prompt language and apply remote-policy cap in `score_one`.
- Modify `app/services/match_service.py`: apply remote-policy cap in graph-based scoring and centralize pass/fail status.
- Modify `app/worker/handlers/match.py`: set `Application.status` from threshold result, matching synchronous behavior.
- Modify `tests/unit/test_matching_agent.py`: prompt assertions for office-attendance policy.
- Modify `tests/unit/test_match_service.py`: behavioral score/status tests for remote-only and target-location cases.
- Modify `tests/integration/test_job_sync.py`: prove long raw/clean descriptions are preserved beyond 20k chars.
- Modify `README.md`: document the production full-reset command and smoke-user reseed step because wipe semantics now include users/profiles.

---

### Task 1: Atomic Full Reset Script

**Files:**
- Modify: `scripts/wipe_job_data.py`
- Test: `tests/integration/test_wipe_job_data.py`

- [ ] **Step 1: Write failing integration tests for full reset scope**

Create `tests/integration/test_wipe_job_data.py`:

```python
import uuid

import pytest
from sqlalchemy import text

from scripts.wipe_job_data import wipe


async def _count(session, table: str) -> int:
    result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_wipe_removes_user_owned_and_job_search_rows_but_preserves_companies(db_session):
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    company_id = uuid.uuid4()
    job_id = uuid.uuid4()
    app_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    await db_session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:user_id, 'wipe@example.com', '', TRUE, FALSE, TRUE)
        """),
        {"user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO user_profiles (id, user_id, email, target_roles, target_locations,
                source_cursors, target_company_slugs, target_company_ids, search_active,
                created_at, updated_at)
            VALUES (:profile_id, :user_id, 'wipe@example.com', '{}', '{}',
                '{}', '{}', '{}', TRUE, now(), now())
        """),
        {"profile_id": profile_id, "user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs,
                resolved_at, created_at)
            VALUES (:company_id, 'Acme', :key, '{"greenhouse":"acme"}', now(), now())
        """),
        {"company_id": company_id, "key": f"acme-{uuid.uuid4()}"},
    )
    await db_session.execute(
        text("""
            INSERT INTO jobs (id, source, external_id, title, company_name, company_id,
                description_raw, description, apply_url, fetched_at, is_active)
            VALUES (:job_id, 'greenhouse', 'wipe-job', 'Engineer', 'Acme', :company_id,
                '<p>raw</p>', 'raw', 'https://example.com', now(), TRUE)
        """),
        {"job_id": job_id, "company_id": company_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO applications (id, job_id, profile_id, status, generation_status,
                match_status, match_attempts, generation_attempts, match_strengths,
                match_gaps, created_at, updated_at)
            VALUES (:app_id, :job_id, :profile_id, 'pending_review', 'ready',
                'matched', 0, 0, '{}', '{}', now(), now())
        """),
        {"app_id": app_id, "job_id": job_id, "profile_id": profile_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO generated_documents (id, application_id, doc_type, content_md, created_at)
            VALUES (:doc_id, :app_id, 'cover_letter', 'hello', now())
        """),
        {"doc_id": doc_id, "app_id": app_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO work_queue (job_type, payload, status, dedupe_key)
            VALUES ('match', '{"application_id":"x"}', 'pending', 'match:x')
        """)
    )
    await db_session.commit()

    await wipe(db_session)

    assert await _count(db_session, "users") == 0
    assert await _count(db_session, "user_profiles") == 0
    assert await _count(db_session, "applications") == 0
    assert await _count(db_session, "generated_documents") == 0
    assert await _count(db_session, "jobs") == 0
    assert await _count(db_session, "work_queue") == 0
    assert await _count(db_session, "companies") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/integration/test_wipe_job_data.py::test_wipe_removes_user_owned_and_job_search_rows_but_preserves_companies -v
```

Expected: FAIL because `wipe()` currently preserves user/profile rows and does not truncate `work_queue`.

- [ ] **Step 3: Implement full reset table sets and atomic transaction**

In `scripts/wipe_job_data.py`, replace the table constants and `wipe()` body with this shape:

```python
WIPE_TABLES = (
    "generated_documents",
    "applications",
    "jobs",
    "work_queue",
    "events",
    "oauth_accounts",
    "skills",
    "work_experiences",
    "user_profiles",
    "users",
    "llm_status",
    "rate_limits",
    "usage_counters",
)

CHECKPOINT_WIPE_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)

PRESERVE_TABLES = (
    "companies",
    "slug_fetches",
)


async def _existing_tables(session: AsyncSession, tables: tuple[str, ...]) -> tuple[str, ...]:
    result = await session.execute(
        text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
        """),
        {"tables": list(tables)},
    )
    existing = {row[0] for row in result.all()}
    return tuple(table for table in tables if table in existing)


async def wipe(session: AsyncSession, *, fail_after_mutation: bool = False) -> None:
    required_tables = await _existing_tables(session, WIPE_TABLES)
    checkpoint_tables = await _existing_tables(session, CHECKPOINT_WIPE_TABLES)
    preserve_tables = await _existing_tables(session, PRESERVE_TABLES)

    print("\nBEFORE - wiped tables:")
    for t, n in (await _counts(session, required_tables + checkpoint_tables)).items():
        print(f"  {t:25s} {n:>10,}")
    print("\nBEFORE - preserved tables:")
    for t, n in (await _counts(session, preserve_tables)).items():
        print(f"  {t:25s} {n:>10,}")

    joined = ", ".join(required_tables + checkpoint_tables)
    print(f"\nTruncating: {joined}")
    await session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))  # noqa: S608
    await session.execute(
        text("""
            UPDATE slug_fetches
            SET last_fetched_at = NULL,
                last_attempted_at = NULL,
                queued_at = NULL,
                claimed_at = NULL,
                last_status = NULL,
                consecutive_5xx_count = 0
            WHERE is_invalid = FALSE
        """)
    )
    if fail_after_mutation:
        await session.rollback()
        raise RuntimeError("injected failure after reset mutation")
    await session.commit()

    print("\nAFTER - wiped tables:")
    for t, n in (await _counts(session, required_tables + checkpoint_tables)).items():
        print(f"  {t:25s} {n:>10,}")
    print("\nAFTER - preserved tables:")
    for t, n in (await _counts(session, preserve_tables)).items():
        print(f"  {t:25s} {n:>10,}")
```

- [ ] **Step 4: Add slug freshness and invalid preservation tests**

Append to `tests/integration/test_wipe_job_data.py`:

```python
@pytest.mark.asyncio
async def test_wipe_resets_non_invalid_slug_fetches_and_preserves_invalid_rows(db_session):
    await db_session.execute(
        text("""
            INSERT INTO slug_fetches (source, slug, last_fetched_at, last_attempted_at,
                last_status, consecutive_404_count, consecutive_5xx_count, is_invalid,
                invalid_reason, queued_at, claimed_at)
            VALUES
              ('greenhouse', 'validco', now(), now(), 'ok', 0, 3, FALSE, NULL, now(), now()),
              ('greenhouse', 'deadco', now(), now(), 'invalid', 2, 0, TRUE, 'board not found', now(), now())
        """)
    )
    await db_session.commit()

    await wipe(db_session)

    rows = (
        await db_session.execute(
            text("""
                SELECT slug, last_fetched_at, last_attempted_at, last_status,
                       consecutive_404_count, consecutive_5xx_count, is_invalid,
                       invalid_reason, queued_at, claimed_at
                FROM slug_fetches
                ORDER BY slug
            """)
        )
    ).mappings().all()
    by_slug = {row["slug"]: row for row in rows}

    assert by_slug["validco"]["last_fetched_at"] is None
    assert by_slug["validco"]["last_attempted_at"] is None
    assert by_slug["validco"]["last_status"] is None
    assert by_slug["validco"]["consecutive_5xx_count"] == 0
    assert by_slug["validco"]["queued_at"] is None
    assert by_slug["validco"]["claimed_at"] is None

    assert by_slug["deadco"]["is_invalid"] is True
    assert by_slug["deadco"]["consecutive_404_count"] == 2
    assert by_slug["deadco"]["invalid_reason"] == "board not found"
```

- [ ] **Step 5: Add checkpoint cleanup test**

Append:

```python
@pytest.mark.asyncio
async def test_wipe_clears_checkpoint_tables_when_present(db_session):
    await db_session.execute(text("CREATE TABLE checkpoints (thread_id text primary key)"))
    await db_session.execute(text("INSERT INTO checkpoints (thread_id) VALUES ('profile-thread')"))
    await db_session.commit()

    await wipe(db_session)

    assert await _count(db_session, "checkpoints") == 0
```

- [ ] **Step 6: Add rollback test**

Append:

```python
@pytest.mark.asyncio
async def test_wipe_rolls_back_when_failure_is_injected(db_session):
    user_id = uuid.uuid4()
    await db_session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:user_id, 'rollback@example.com', '', TRUE, FALSE, TRUE)
        """),
        {"user_id": user_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO slug_fetches (source, slug, last_fetched_at, last_status, is_invalid)
            VALUES ('greenhouse', 'validco', now(), 'ok', FALSE)
        """)
    )
    await db_session.commit()

    with pytest.raises(RuntimeError, match="injected failure"):
        await wipe(db_session, fail_after_mutation=True)

    assert await _count(db_session, "users") == 1
    row = (
        await db_session.execute(
            text("SELECT last_fetched_at, last_status FROM slug_fetches WHERE slug = 'validco'")
        )
    ).one()
    assert row.last_fetched_at is not None
    assert row.last_status == "ok"
```

- [ ] **Step 7: Run reset tests**

Run:

```bash
uv run pytest tests/integration/test_wipe_job_data.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit reset script**

```bash
git add scripts/wipe_job_data.py tests/integration/test_wipe_job_data.py
git commit -m "feat(ops): add full data reset script"
```

---

### Task 2: Preserve Long Raw Descriptions

**Files:**
- Modify: `tests/integration/test_job_sync.py`
- Inspect only if the regression test fails: `app/services/job_service.py`, `app/services/html_cleaner.py`

- [ ] **Step 1: Add long-description ingestion regression test**

Append to `tests/integration/test_job_sync.py`:

```python
@pytest.mark.asyncio
async def test_upsert_job_preserves_description_beyond_prompt_cap(db_session):
    from app.agents.matching_agent import MAX_JOB_DESC_CHARS, truncate_description

    raw_body = "remote policy detail " * 1200
    raw = f"<h2>Role</h2><p>{raw_body}</p>"
    assert len(raw) > MAX_JOB_DESC_CHARS
    data = JobData(
        external_id="ext-long-desc",
        title="Long Description Engineer",
        company_name="Test Co",
        location="Remote",
        workplace_type="remote",
        description_raw=raw,
        salary=None,
        contract_type=None,
        apply_url="https://example.com/apply/long",
        posted_at=None,
    )

    job, created = await upsert_job(data, "greenhouse", db_session)

    assert created is True
    assert job.description_raw == raw
    assert job.description is not None
    assert len(job.description) > MAX_JOB_DESC_CHARS
    prompt_fragment = truncate_description(job.description)
    assert len(prompt_fragment) < len(job.description)
    assert prompt_fragment.endswith("[Description truncated]")
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/integration/test_job_sync.py::test_upsert_job_preserves_description_beyond_prompt_cap -v
```

Expected: PASS. This locks the existing storage boundary so future edits cannot move truncation into ingestion.

- [ ] **Step 3: Commit raw-preservation test**

```bash
git add tests/integration/test_job_sync.py
git commit -m "test(jobs): assert full description storage"
```

---

### Task 3: Deterministic Remote Policy Guard

**Files:**
- Create: `app/services/remote_policy.py`
- Create: `tests/unit/test_remote_policy.py`

- [ ] **Step 1: Write failing unit tests**

Create `tests/unit/test_remote_policy.py`:

```python
from types import SimpleNamespace

from app.services.remote_policy import evaluate_remote_policy


def _profile(*, remote_ok=True, target_locations=None):
    return SimpleNamespace(remote_ok=remote_ok, target_locations=target_locations or [])


def _job(*, location="Remote", workplace_type="remote", description=""):
    return SimpleNamespace(
        location=location,
        workplace_type=workplace_type,
        description=description,
        description_raw=description,
    )


def test_remote_only_profile_rejects_required_office_attendance():
    verdict = evaluate_remote_policy(
        _profile(remote_ok=True, target_locations=[]),
        _job(description="This role requires a minimum 3 days/week in the Toronto office."),
    )

    assert verdict.hard_mismatch is True
    assert "office attendance" in verdict.gap.lower()


def test_target_location_allows_matching_hybrid_office():
    verdict = evaluate_remote_policy(
        _profile(remote_ok=True, target_locations=["Toronto"]),
        _job(description="This role requires a minimum 3 days/week in the Toronto office."),
    )

    assert verdict.hard_mismatch is False


def test_provider_remote_does_not_override_jd_office_requirement():
    verdict = evaluate_remote_policy(
        _profile(remote_ok=True, target_locations=[]),
        _job(
            location="Remote",
            workplace_type="remote",
            description="Remote role, but candidates must work from the NYC office twice a week.",
        ),
    )

    assert verdict.hard_mismatch is True
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_remote_policy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.remote_policy'`.

- [ ] **Step 3: Implement minimal remote policy helper**

Create `app/services/remote_policy.py`:

```python
from dataclasses import dataclass
from typing import Protocol


class ProfileLike(Protocol):
    remote_ok: bool
    target_locations: list[str] | None


class JobLike(Protocol):
    location: str | None
    workplace_type: str | None
    description: str | None
    description_raw: str | None


@dataclass(frozen=True)
class RemotePolicyVerdict:
    hard_mismatch: bool
    gap: str | None = None


OFFICE_REQUIREMENT_PHRASES = (
    "minimum",
    "days/week",
    "days per week",
    "in office",
    "office",
    "hybrid schedule required",
    "must work from",
    "must be located near",
    "onsite",
    "on-site",
)


def _text(job: JobLike) -> str:
    return " ".join(
        part
        for part in (
            job.location or "",
            job.workplace_type or "",
            job.description or "",
            job.description_raw or "",
        )
        if part
    ).lower()


def _requires_office_attendance(text: str) -> bool:
    office_words = ("office", "onsite", "on-site", "hybrid")
    requirement_words = (
        "minimum",
        "required",
        "requires",
        "must",
        "days/week",
        "days per week",
        "work from",
        "located near",
    )
    return any(word in text for word in office_words) and any(
        word in text for word in requirement_words
    )


def _matches_target_location(text: str, target_locations: list[str]) -> bool:
    normalized_targets = [loc.lower().strip() for loc in target_locations if loc.strip()]
    return any(target and target in text for target in normalized_targets)


def evaluate_remote_policy(profile: ProfileLike, job: JobLike) -> RemotePolicyVerdict:
    text = _text(job)
    target_locations = list(profile.target_locations or [])
    if not _requires_office_attendance(text):
        return RemotePolicyVerdict(hard_mismatch=False)
    if target_locations and _matches_target_location(text, target_locations):
        return RemotePolicyVerdict(hard_mismatch=False)
    gap = "Requires recurring office attendance outside target locations"
    return RemotePolicyVerdict(hard_mismatch=True, gap=gap)
```

- [ ] **Step 4: Run unit tests**

```bash
uv run pytest tests/unit/test_remote_policy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit helper**

```bash
git add app/services/remote_policy.py tests/unit/test_remote_policy.py
git commit -m "feat(matching): add remote policy guard"
```

---

### Task 4: Matching Prompt And Score Cap Enforcement

**Files:**
- Modify: `app/agents/matching_agent.py`
- Modify: `app/services/match_service.py`
- Modify: `app/worker/handlers/match.py`
- Modify: `tests/unit/test_matching_agent.py`
- Modify: `tests/unit/test_match_service.py`

- [ ] **Step 1: Add prompt assertions**

Modify `tests/unit/test_matching_agent.py` by adding:

```python
def test_system_prompt_makes_required_office_attendance_hard_mismatch():
    assert "required recurring office attendance" in SCORING_SYSTEM_PROMPT
    assert "provider metadata says remote" in SCORING_SYSTEM_PROMPT
    assert "below the match threshold" in SCORING_SYSTEM_PROMPT
    assert "minimum 2 days/week in office" in SCORING_SYSTEM_PROMPT
```

- [ ] **Step 2: Run prompt test to verify failure**

```bash
uv run pytest tests/unit/test_matching_agent.py::test_system_prompt_makes_required_office_attendance_hard_mismatch -v
```

Expected: FAIL because the current prompt does not contain this policy.

- [ ] **Step 3: Update matching prompt**

In `app/agents/matching_agent.py`, replace the `Location:` section of `SCORING_SYSTEM_PROMPT` with:

```python
Location and work mode:
- JD location is in candidate target locations OR (JD fully remote AND candidate remote): not a gap.
- A job is fully remote only when the candidate can perform it without required recurring office attendance.
- Required recurring office attendance makes the job hybrid/onsite even if provider metadata says remote.
- Phrases like "minimum 2 days/week in office", "must work from the Toronto office", "hybrid schedule required", or "must be located near NYC/SF" are authoritative.
- For remote-only candidates, required office attendance is a hard mismatch and must score below the match threshold.
- For candidates with target locations, hybrid/onsite is acceptable only when the office location matches a target location.
- If provider metadata and JD prose conflict, JD prose wins.
- Otherwise: hard gap, e.g., "Onsite Seattle, candidate based in CA".
- Never say "may require clarification" or "depends". Decide.
```

- [ ] **Step 4: Add score-cap helper in match service**

In `app/services/match_service.py`, add near the imports:

```python
def apply_remote_policy_to_score(score_result, profile: UserProfile, job: Job, threshold: float):
    from app.services.remote_policy import evaluate_remote_policy

    verdict = evaluate_remote_policy(profile, job)
    if not verdict.hard_mismatch or score_result.score is None:
        return score_result
    cap = min(0.29, threshold - 0.01)
    if score_result.score >= threshold:
        score_result.score = max(0.0, cap)
    if verdict.gap and verdict.gap not in score_result.gaps:
        score_result.gaps = [*score_result.gaps, verdict.gap]
    if verdict.gap and verdict.gap not in score_result.rationale:
        score_result.rationale = f"{score_result.rationale} {verdict.gap}".strip()
    return score_result
```

- [ ] **Step 5: Apply cap in graph scoring path**

In `score_and_match()`, before persisting each `score_result`, find the job from `job_map` and apply the guard:

```python
        job = job_map.get(score_result.application_id)
        if job is not None:
            score_result = apply_remote_policy_to_score(
                score_result,
                profile,
                job,
                settings.match_score_threshold,
            )
```

Place this before the `if score_result.score is None:` block.

- [ ] **Step 6: Apply cap in single-application worker scoring path**

In `app/agents/matching_agent.py`, inside `score_one()` after `score = await score_job_context(...)`, add:

```python
    from app.config import get_settings
    from app.services.match_service import apply_remote_policy_to_score

    score = apply_remote_policy_to_score(
        score,
        profile,
        job,
        get_settings().match_score_threshold,
    )
```

- [ ] **Step 7: Align worker pass/fail status with synchronous matching**

In `app/worker/handlers/match.py`, add the module-level import:

```python
from app.config import get_settings
```

Then set status after the score result:

```python
        settings = get_settings()
        passed = result["score"] is not None and result["score"] >= settings.match_score_threshold
        app.status = "pending_review" if passed else "auto_rejected"
```

Place it before `session.add(app)`.

- [ ] **Step 8: Add behavioral score-cap tests**

Append to `tests/unit/test_match_service.py`:

```python
def test_remote_policy_caps_remote_only_office_attendance_score():
    from app.agents.matching_agent import ScoreResult
    from app.models.job import Job
    from app.services.match_service import apply_remote_policy_to_score

    profile = _profile(target_locations=[], remote_ok=True)
    job = Job(
        source="greenhouse",
        external_id="remote-policy-1",
        title="Engineer",
        company_name="Acme",
        location="Remote",
        workplace_type="remote",
        description="This role requires a minimum 3 days/week in the Toronto office.",
        apply_url="https://example.com",
    )
    score = ScoreResult(
        application_id="00000000-0000-0000-0000-000000000000",
        score=0.92,
        summary="Backend role, remote metadata.",
        rationale="Strong stack match.",
        strengths=["Python"],
        gaps=[],
    )

    adjusted = apply_remote_policy_to_score(score, profile, job, threshold=0.65)

    assert adjusted.score < 0.65
    assert any("office attendance" in gap.lower() for gap in adjusted.gaps)


def test_remote_policy_does_not_cap_matching_target_location():
    from app.agents.matching_agent import ScoreResult
    from app.models.job import Job
    from app.services.match_service import apply_remote_policy_to_score

    profile = _profile(target_locations=["Toronto"], remote_ok=True)
    job = Job(
        source="greenhouse",
        external_id="remote-policy-2",
        title="Engineer",
        company_name="Acme",
        location="Toronto",
        workplace_type="hybrid",
        description="This role requires a minimum 3 days/week in the Toronto office.",
        apply_url="https://example.com",
    )
    score = ScoreResult(
        application_id="00000000-0000-0000-0000-000000000000",
        score=0.92,
        summary="Backend role, Toronto hybrid.",
        rationale="Strong stack match.",
        strengths=["Python"],
        gaps=[],
    )

    adjusted = apply_remote_policy_to_score(score, profile, job, threshold=0.65)

    assert adjusted.score == 0.92
    assert adjusted.gaps == []
```

- [ ] **Step 9: Add worker status test**

Create `tests/unit/test_match_handler.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.application import Application
from app.models.work_queue import WorkQueue
from app.worker.handlers.match import MatchHandler


@pytest.mark.asyncio
async def test_match_handler_auto_rejects_below_threshold_score():
    app = Application(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        match_status="pending_match",
        match_strengths=[],
        match_gaps=[],
    )
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = app
    session.execute.return_value = result
    session.add = MagicMock()
    row = WorkQueue(payload={"application_id": str(app.id)})

    settings = MagicMock()
    settings.match_score_threshold = 0.65

    with (
        patch("app.agents.matching_agent.score_one", new=AsyncMock(return_value={
            "score": 0.29,
            "summary": "Hybrid Toronto role.",
            "rationale": "Requires office attendance.",
            "strengths": ["Python"],
            "gaps": ["Requires recurring office attendance outside target locations"],
        })),
        patch("app.worker.handlers.match.get_settings", return_value=settings),
    ):
        await MatchHandler()(session, row)

    assert app.status == "auto_rejected"
    assert app.match_status == "matched"
```

- [ ] **Step 10: Run matching tests**

```bash
uv run pytest tests/unit/test_remote_policy.py tests/unit/test_matching_agent.py tests/unit/test_match_service.py tests/unit/test_match_handler.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit matching policy**

```bash
git add app/services/remote_policy.py app/agents/matching_agent.py app/services/match_service.py app/worker/handlers/match.py tests/unit/test_remote_policy.py tests/unit/test_matching_agent.py tests/unit/test_match_service.py tests/unit/test_match_handler.py
git commit -m "fix(matching): enforce remote-only office mismatch"
```

---

### Task 5: Final Verification And Operator Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run focused backend tests**

```bash
uv run pytest tests/unit/test_remote_policy.py tests/unit/test_matching_agent.py tests/unit/test_match_service.py tests/unit/test_match_handler.py tests/integration/test_wipe_job_data.py tests/integration/test_job_sync.py -v
```

Expected: PASS.

- [ ] **Step 2: Run broader unit tests**

```bash
uv run pytest tests/unit/ -v
```

Expected: PASS.

- [ ] **Step 3: Run broader integration tests**

```bash
uv run pytest tests/integration/ -v
```

Expected: PASS.

- [ ] **Step 4: Update operator docs**

Add this to `README.md` under Dev commands:

````markdown
### Production data repair

This destructive reset is for pre-launch data repairs only. It wipes users,
profiles, resumes, applications, generated documents, jobs, queues, usage state,
and LangGraph checkpoints. It preserves companies and invalid slug evidence,
then resets non-invalid slug freshness so the recreated owner profile can fetch
fresh jobs.

Run against production only after workers/cron drainers are paused:

```bash
DATABASE_URL=postgresql+asyncpg://... uv run python scripts/wipe_job_data.py --yes-i-mean-prod
make seed-smoke-user
```

Afterward, sign in again, recreate the owner profile/resume/followed companies,
verify `target_company_ids` is non-empty, and trigger sync.
````

- [ ] **Step 5: Commit operator docs**

```bash
git add README.md
git commit -m "docs: document full data reset flow"
```

- [ ] **Step 6: Final status**

Collect:

```bash
git status --short
git log --oneline -5
```

Expected: working tree clean, recent commits include reset script, remote policy, raw-description regression, and reset docs.
