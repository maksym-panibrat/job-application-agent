# Worker Lanes And Match Prefilter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one physical worker process with separate in-process LLM and slow-lane pools, and skip LLM matching for deterministic hard mismatches including non-US jobs and remote-policy failures.

**Architecture:** Keep one `work_queue` table and add filtered claiming by job type. The worker starts either the current single unfiltered pool or two named lane pools from env config. The match handler loads the application, job, and profile, rejects jobs that are not explicitly US-based using only job-owned fields, then runs `evaluate_remote_policy()` before `matching_agent.score_one()`, and persists visible deterministic rejections.

**Tech Stack:** Python 3.12, FastAPI app code, SQLModel/SQLAlchemy async sessions, Postgres `FOR UPDATE SKIP LOCKED`, pytest, pytest-asyncio.

---

## File Structure

- Modify `app/worker/queue_service.py`: add optional `job_types` filtering to `claim_one()`.
- Modify `tests/integration/test_queue_service.py`: prove filtered claiming preserves FIFO, `not_before`, and stale lease behavior.
- Modify `app/worker/config.py`: add lane env vars and parsing helpers to `WorkerSettings`.
- Modify `app/worker/main.py`: introduce lane pool orchestration inside one process and pass lane allowlists to `claim_one()`.
- Modify `tests/integration/test_worker_lifecycle.py`: prove one process runs independent LLM and slow pools.
- Modify `app/worker/handlers/match.py`: add deterministic pre-LLM non-US and remote-policy rejection.
- Modify `tests/integration/test_handler_match.py`: prove visible deterministic rejection and no LLM call.
- Optional docs check only: `docs/superpowers/specs/2026-05-14-worker-lanes-and-match-prefilter-design.md` already defines the behavior and should not need implementation edits.

## Task 1: Filtered Queue Claiming

**Files:**
- Modify: `app/worker/queue_service.py`
- Test: `tests/integration/test_queue_service.py`

- [ ] **Step 1: Write failing tests for job-type filtered claims**

Append these tests to `tests/integration/test_queue_service.py`:

```python
@pytest.mark.asyncio
async def test_claim_one_filters_to_allowed_job_types(db_session):
    fetch_id = await enqueue(db_session, job_type="fetch-slug", payload={"order": "old"})
    match_id = await enqueue(db_session, job_type="match", payload={"order": "new"})
    await db_session.execute(
        text("UPDATE work_queue SET enqueued_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": fetch_id},
    )
    await db_session.commit()

    claimed = await claim_one(
        db_session,
        worker_id="llm-worker",
        visibility_timeout_s=600,
        job_types=["match", "generate-cover-letter"],
    )
    await db_session.commit()

    assert claimed is not None
    assert claimed.id == match_id
    assert claimed.job_type == "match"


@pytest.mark.asyncio
async def test_claim_one_job_type_filter_preserves_not_before(db_session):
    row_id = await enqueue(db_session, job_type="match", payload={})
    await db_session.execute(
        text(
            "UPDATE work_queue SET not_before = now() + interval '5 minutes' "
            "WHERE id = :id"
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(
        db_session,
        worker_id="llm-worker",
        visibility_timeout_s=600,
        job_types=["match"],
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_one_job_type_filter_reclaims_matching_stale_row(db_session):
    row_id = await enqueue(db_session, job_type="match", payload={})
    await db_session.execute(
        text(
            """
            UPDATE work_queue
            SET status='in_progress',
                claimed_at = now() - interval '700 seconds',
                claimed_by = 'dead-worker',
                attempts = 1
            WHERE id = :id
            """
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(
        db_session,
        worker_id="llm-worker",
        visibility_timeout_s=600,
        job_types=["match"],
    )
    await db_session.commit()

    assert claimed is not None
    assert claimed.id == row_id
    assert claimed.claimed_by == "llm-worker"
    assert claimed.attempts == 2


@pytest.mark.asyncio
async def test_claim_one_job_type_filter_ignores_other_stale_rows(db_session):
    row_id = await enqueue(db_session, job_type="fetch-slug", payload={})
    await db_session.execute(
        text(
            """
            UPDATE work_queue
            SET status='in_progress',
                claimed_at = now() - interval '700 seconds',
                claimed_by = 'dead-worker',
                attempts = 1
            WHERE id = :id
            """
        ),
        {"id": row_id},
    )
    await db_session.commit()

    claimed = await claim_one(
        db_session,
        worker_id="llm-worker",
        visibility_timeout_s=600,
        job_types=["match", "generate-cover-letter"],
    )

    assert claimed is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_queue_service.py::test_claim_one_filters_to_allowed_job_types tests/integration/test_queue_service.py::test_claim_one_job_type_filter_preserves_not_before tests/integration/test_queue_service.py::test_claim_one_job_type_filter_reclaims_matching_stale_row tests/integration/test_queue_service.py::test_claim_one_job_type_filter_ignores_other_stale_rows -v
```

Expected: FAIL with `TypeError: claim_one() got an unexpected keyword argument 'job_types'`.

- [ ] **Step 3: Implement filtered claiming**

In `app/worker/queue_service.py`, change the `claim_one()` signature and query setup to this shape:

```python
async def claim_one(
    session: AsyncSession,
    *,
    worker_id: str,
    visibility_timeout_s: int,
    job_types: list[str] | tuple[str, ...] | None = None,
) -> WorkQueue | None:
    """Atomically claim the next pending or stale non-self in-progress row."""
    params: dict[str, Any] = {
        "timeout": visibility_timeout_s,
        "worker_id": worker_id,
    }
    job_type_filter = ""
    if job_types is not None:
        normalized_job_types = [job_type for job_type in job_types if job_type]
        if not normalized_job_types:
            return None
        params["job_types"] = normalized_job_types
        job_type_filter = "AND job_type = ANY(CAST(:job_types AS text[]))"

    result = await session.execute(
        text(
            f"""
            WITH claimed AS (
              SELECT id
              FROM work_queue
              WHERE (not_before IS NULL OR not_before <= now())
                {job_type_filter}
                AND (
                  status = 'pending'
                  OR (
                    status = 'in_progress'
                    AND claimed_at < now() - make_interval(secs => :timeout)
                    AND (claimed_by IS NULL OR claimed_by <> :worker_id)
                  )
                )
              ORDER BY enqueued_at ASC
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE work_queue
            SET status = 'in_progress',
                claimed_at = now(),
                claimed_by = :worker_id,
                attempts = work_queue.attempts + 1
            FROM claimed
            WHERE work_queue.id = claimed.id
            RETURNING work_queue.id, work_queue.job_type, work_queue.payload,
                      work_queue.status, work_queue.enqueued_at,
                      work_queue.claimed_at, work_queue.claimed_by,
                      work_queue.not_before, work_queue.completed_at,
                      work_queue.attempts, work_queue.last_error,
                      work_queue.dedupe_key
            """
        ),
        params,
    )
```

Keep the existing row-to-`WorkQueue` mapping unchanged below this block.

- [ ] **Step 4: Run queue service tests**

Run:

```bash
uv run pytest tests/integration/test_queue_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker/queue_service.py tests/integration/test_queue_service.py
git commit -m "feat(worker): filter queue claims by job type"
```

## Task 2: Worker Lane Configuration

**Files:**
- Modify: `app/worker/config.py`
- Test: `tests/unit/test_worker_config.py`

- [ ] **Step 1: Write failing config tests**

Append these tests to `tests/unit/test_worker_config.py`:

```python
def test_worker_settings_default_single_pool(monkeypatch):
    monkeypatch.delenv("WORKER_LLM_JOB_TYPES", raising=False)
    monkeypatch.delenv("WORKER_SLOW_JOB_TYPES", raising=False)

    settings = WorkerSettings()

    assert settings.lanes_enabled is False
    assert settings.lane_configs() == [
        WorkerLane(name="default", job_types=None, concurrency=settings.concurrency)
    ]


def test_worker_settings_parses_lane_job_types(monkeypatch):
    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", " match, generate-cover-letter,match ")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "6")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "fetch-slug, maintenance")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "20")

    settings = WorkerSettings()

    assert settings.lanes_enabled is True
    assert settings.lane_configs() == [
        WorkerLane(
            name="llm",
            job_types=("match", "generate-cover-letter"),
            concurrency=6,
        ),
        WorkerLane(
            name="slow",
            job_types=("fetch-slug", "maintenance"),
            concurrency=20,
        ),
    ]
```

Ensure the top of `tests/unit/test_worker_config.py` imports both names:

```python
from app.worker.config import WorkerLane, WorkerSettings
```

- [ ] **Step 2: Run config tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_worker_config.py -v
```

Expected: FAIL because `WorkerLane`, `lanes_enabled`, and `lane_configs()` do not exist.

- [ ] **Step 3: Implement lane config parsing**

Replace `app/worker/config.py` with:

```python
"""Worker-process configuration. Spec § Concurrency knobs."""
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class WorkerLane:
    name: str
    job_types: tuple[str, ...] | None
    concurrency: int


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_")

    concurrency: int = 4
    poll_interval_s: int = 3
    visibility_timeout_s: int = 600
    drain_budget_s: int = 80
    transient_backoff_base_s: int = 30
    transient_backoff_max_s: int = 300
    unknown_type_backoff_s: int = 300
    mark_done_retry_backoff_s: int = 60

    llm_job_types: str | None = None
    llm_concurrency: int = 6
    slow_job_types: str | None = None
    slow_concurrency: int = 20

    @property
    def lanes_enabled(self) -> bool:
        return bool(self.llm_job_types or self.slow_job_types)

    def lane_configs(self) -> list[WorkerLane]:
        if not self.lanes_enabled:
            return [
                WorkerLane(
                    name="default",
                    job_types=None,
                    concurrency=self.concurrency,
                )
            ]

        lanes: list[WorkerLane] = []
        llm_types = _parse_job_types(self.llm_job_types)
        slow_types = _parse_job_types(self.slow_job_types)
        if llm_types:
            lanes.append(
                WorkerLane(
                    name="llm",
                    job_types=llm_types,
                    concurrency=self.llm_concurrency,
                )
            )
        if slow_types:
            lanes.append(
                WorkerLane(
                    name="slow",
                    job_types=slow_types,
                    concurrency=self.slow_concurrency,
                )
            )
        if not lanes:
            return [
                WorkerLane(
                    name="default",
                    job_types=None,
                    concurrency=self.concurrency,
                )
            ]
        return lanes


def _parse_job_types(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()

    seen: set[str] = set()
    parsed: list[str] = []
    for item in raw.split(","):
        job_type = item.strip()
        if not job_type or job_type in seen:
            continue
        seen.add(job_type)
        parsed.append(job_type)
    return tuple(parsed)
```

- [ ] **Step 4: Run config tests**

Run:

```bash
uv run pytest tests/unit/test_worker_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker/config.py tests/unit/test_worker_config.py
git commit -m "feat(worker): add lane configuration"
```

## Task 3: In-Process Worker Lane Pools

**Files:**
- Modify: `app/worker/main.py`
- Test: `tests/integration/test_worker_lifecycle.py`

- [ ] **Step 1: Write failing worker lifecycle tests**

Append these tests to `tests/integration/test_worker_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_worker_lanes_process_allowed_job_types(db_session, monkeypatch):
    from app.worker.handlers import HANDLERS

    calls: list[str] = []

    class Record:
        max_attempts = 3

        def __init__(self, name: str) -> None:
            self.name = name

        async def __call__(self, session, row):
            calls.append(self.name)

    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", "test-llm")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "test-slow")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "test-llm", Record("llm"))
    monkeypatch.setitem(HANDLERS, "test-slow", Record("slow"))

    await enqueue(db_session, job_type="test-llm", payload={})
    await enqueue(db_session, job_type="test-slow", payload={})
    await db_session.commit()

    async def stop_soon():
        while len(calls) < 2:
            await asyncio.sleep(0.05)
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_soon())

    assert sorted(calls) == ["llm", "slow"]


@pytest.mark.asyncio
async def test_slow_lane_drains_while_llm_lane_is_saturated(db_session, monkeypatch):
    started_llm = asyncio.Event()
    release_llm = asyncio.Event()
    slow_done = asyncio.Event()
    from app.worker.handlers import HANDLERS

    class SlowLlm:
        max_attempts = 3

        async def __call__(self, session, row):
            started_llm.set()
            await release_llm.wait()

    class FastSlow:
        max_attempts = 3

        async def __call__(self, session, row):
            slow_done.set()

    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", "test-llm-blocking")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "test-slow-fast")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "1")
    monkeypatch.setitem(HANDLERS, "test-llm-blocking", SlowLlm())
    monkeypatch.setitem(HANDLERS, "test-slow-fast", FastSlow())

    await enqueue(db_session, job_type="test-llm-blocking", payload={})
    await enqueue(db_session, job_type="test-slow-fast", payload={})
    await db_session.commit()

    async def stop_after_slow_done():
        await started_llm.wait()
        await asyncio.wait_for(slow_done.wait(), timeout=2)
        release_llm.set()
        worker_main._shutdown.set()

    await asyncio.gather(worker_main.run(), stop_after_slow_done())

    statuses = (
        await db_session.execute(
            text(
                """
                SELECT job_type, status
                FROM work_queue
                WHERE job_type IN ('test-llm-blocking', 'test-slow-fast')
                ORDER BY job_type
                """
            )
        )
    ).all()
    assert statuses == [
        ("test-llm-blocking", "done"),
        ("test-slow-fast", "done"),
    ]
```

- [ ] **Step 2: Run worker lifecycle tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_worker_lifecycle.py::test_worker_lanes_process_allowed_job_types tests/integration/test_worker_lifecycle.py::test_slow_lane_drains_while_llm_lane_is_saturated -v
```

Expected: FAIL because the current worker has one shared pool and does not read lane env vars.

- [ ] **Step 3: Add lane-aware worker pool helpers**

In `app/worker/main.py`, update imports:

```python
from app.worker.config import WorkerLane, WorkerSettings
```

Change `_handle_one()` to accept a lane name and include it in logs:

```python
async def _handle_one(
    job_row,
    session_factory,
    settings: WorkerSettings,
    *,
    lane: str,
) -> None:
```

Add `lane=lane` to `worker.unknown_job_type`, `worker.transient_failure`, `worker.handler_max_attempts`, `worker.job_failed`, `worker.mark_done_failed`, `worker.mark_done_release_failed`, and `worker.job_done` logs.

Then add this helper above `run()`:

```python
async def _run_lane(
    lane: WorkerLane,
    *,
    settings: WorkerSettings,
    session_factory,
    shutdown_task: asyncio.Task,
) -> None:
    inflight: set[asyncio.Task] = set()
    await log.ainfo(
        "worker.lane_started",
        worker_id=_worker_id,
        lane=lane.name,
        concurrency=lane.concurrency,
        job_types=list(lane.job_types) if lane.job_types is not None else "all",
    )

    while not _shutdown.is_set():
        if len(inflight) >= lane.concurrency:
            await asyncio.wait(
                inflight | {shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            continue

        async with session_factory() as session:
            job = await claim_one(
                session,
                worker_id=_worker_id,
                visibility_timeout_s=settings.visibility_timeout_s,
                job_types=lane.job_types,
            )
            await session.commit()

        if job is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(shutdown_task),
                    timeout=settings.poll_interval_s,
                )
            except TimeoutError:
                pass
            continue

        await log.ainfo(
            "worker.job_start",
            job_id=job.id,
            job_type=job.job_type,
            attempts=job.attempts,
            worker_id=_worker_id,
            lane=lane.name,
        )
        task = asyncio.create_task(
            _handle_one(job, session_factory, settings, lane=lane.name)
        )
        inflight.add(task)
        task.add_done_callback(inflight.discard)

    await log.ainfo(
        "worker.lane_shutdown_drain_start",
        lane=lane.name,
        inflight=len(inflight),
        drain_budget_s=settings.drain_budget_s,
    )
    if inflight:
        await asyncio.wait(inflight, timeout=settings.drain_budget_s)
    await log.ainfo(
        "worker.lane_shutdown_drain_done",
        lane=lane.name,
        inflight=len(inflight),
    )
```

- [ ] **Step 4: Replace `run()` loop with lane orchestration**

In `app/worker/main.py`, keep signal setup and replace the single-pool loop in `run()` with:

```python
    shutdown_task = asyncio.create_task(_shutdown.wait(), name="shutdown-waiter")
    factory = get_session_factory()
    lanes = settings.lane_configs()

    await log.ainfo(
        "worker.started",
        worker_id=_worker_id,
        lanes=[
            {
                "name": lane.name,
                "concurrency": lane.concurrency,
                "job_types": list(lane.job_types) if lane.job_types is not None else "all",
            }
            for lane in lanes
        ],
        visibility_timeout_s=settings.visibility_timeout_s,
    )

    lane_tasks = [
        asyncio.create_task(
            _run_lane(
                lane,
                settings=settings,
                session_factory=factory,
                shutdown_task=shutdown_task,
            ),
            name=f"worker-lane-{lane.name}",
        )
        for lane in lanes
    ]
    await asyncio.wait(lane_tasks, return_when=asyncio.ALL_COMPLETED)
    await log.ainfo("worker.shutdown_done")
```

Remove the old `inflight` loop from `run()` after adding `_run_lane()`.

- [ ] **Step 5: Run worker lifecycle tests**

Run:

```bash
uv run pytest tests/integration/test_worker_lifecycle.py -v
```

Expected: PASS.

- [ ] **Step 6: Run focused worker and queue tests together**

Run:

```bash
uv run pytest tests/integration/test_queue_service.py tests/integration/test_worker_lifecycle.py tests/unit/test_worker_config.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/worker/main.py tests/integration/test_worker_lifecycle.py
git commit -m "feat(worker): run independent in-process lanes"
```

## Task 4: Deterministic Match Prefilter

**Files:**
- Modify: `app/services/remote_policy.py`
- Modify: `app/worker/handlers/match.py`
- Test: `tests/unit/test_remote_policy.py`
- Test: `tests/integration/test_handler_match.py`

**Approved amendment:** The deterministic prefilter also enforces strict
US-only job matching before the LLM call. Use only job-owned fields
(`location`, `workplace_type`, `description`, `description_raw`) for this rule.
Allow the LLM path only when those fields contain an explicit US signal such as
`United States`, `USA`, `U.S.`, `US`, a US state name or abbreviation, or a
recognizable city/state phrase such as `New York, NY`. Reject explicit non-US
postings with no US signal. Reject ambiguous remote postings with no US signal.
Persist the visible rejection with summary
`Deterministic mismatch: non-US position` and gap/rationale
`Position is not US-based`; preserve user-owned statuses.

- [ ] **Step 1: Extend the match handler seed helper**

In `tests/integration/test_handler_match.py`, replace `_seed_application()` with:

```python
async def _seed_application(
    db_session,
    *,
    match_score: float | None = None,
    app_status: str = "pending_review",
    profile_locations: list[str] | None = None,
    job_location: str | None = None,
    workplace_type: str | None = None,
    description: str | None = None,
    description_raw: str | None = None,
) -> Application:
    user = User(id=uuid.uuid4(), email=f"match-handler-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    company = Company(
        canonical_name="Airbnb",
        normalized_key=f"airbnb-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "airbnb"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[company.id],
        target_locations=profile_locations or [],
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Airbnb",
        company_id=company.id,
        location=job_location,
        workplace_type=workplace_type,
        description=description,
        description_raw=description_raw,
        apply_url="https://example.com/job",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        status=app_status,
        match_score=match_score,
        match_strengths=[],
        match_gaps=[],
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app
```

- [ ] **Step 2: Write failing deterministic prefilter tests**

Append these tests to `tests/integration/test_handler_match.py`:

```python
@pytest.mark.asyncio
async def test_match_handler_prefilter_visible_remote_policy_reject(db_session):
    app = await _seed_application(
        db_session,
        description=(
            "Remote role, but candidates must work from the NYC office twice a week."
        ),
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "auto_rejected"
    assert refreshed.match_score is not None
    assert refreshed.match_score < 0.3
    assert refreshed.match_summary == (
        "Deterministic mismatch: recurring office attendance requirement"
    )
    assert refreshed.match_rationale == (
        "Requires recurring office attendance outside target locations"
    )
    assert refreshed.match_strengths == []
    assert refreshed.match_gaps == [
        "Requires recurring office attendance outside target locations"
    ]


@pytest.mark.asyncio
async def test_match_handler_prefilter_preserves_user_owned_status(db_session):
    app = await _seed_application(
        db_session,
        app_status="dismissed",
        description="Candidates must work from the Toronto office twice a week.",
    )
    app_id = app.id
    handler = MatchHandler()

    with patch("app.agents.matching_agent.score_one", AsyncMock()) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    db_session.expire_all()
    refreshed = (
        await db_session.execute(sa.select(Application).where(Application.id == app_id))
    ).scalar_one()
    assert mock_score.call_count == 0
    assert refreshed.status == "dismissed"
    assert refreshed.match_score is not None
    assert refreshed.match_gaps == [
        "Requires recurring office attendance outside target locations"
    ]


@pytest.mark.asyncio
async def test_match_handler_prefilter_allows_target_location_match(db_session):
    app = await _seed_application(
        db_session,
        profile_locations=["Toronto"],
        description="Candidates must work from the Toronto office twice a week.",
    )
    handler = MatchHandler()

    with patch(
        "app.agents.matching_agent.score_one",
        AsyncMock(
            return_value={
                "score": 0.83,
                "summary": "location-compatible hybrid fit",
                "rationale": "Toronto target location matches",
                "strengths": ["Python"],
                "gaps": [],
            }
        ),
    ) as mock_score:
        await handler(db_session, _match_row(app.id))
        await db_session.commit()

    assert mock_score.call_count == 1
```

- [ ] **Step 3: Run match handler tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_handler_match.py::test_match_handler_prefilter_visible_remote_policy_reject tests/integration/test_handler_match.py::test_match_handler_prefilter_preserves_user_owned_status tests/integration/test_handler_match.py::test_match_handler_prefilter_allows_target_location_match -v
```

Expected: FAIL because the handler always calls `matching_agent.score_one()` for unscored applications.

- [ ] **Step 4: Implement deterministic prefilter helper**

In `app/worker/handlers/match.py`, add imports:

```python
from app.models.job import Job
from app.models.user_profile import UserProfile
from app.services.remote_policy import evaluate_remote_policy
```

Add this helper above `MatchHandler`:

```python
def _deterministic_rejection_score(threshold: float) -> float:
    return max(0.0, min(0.29, threshold - 0.01))
```

- [ ] **Step 5: Load job/profile and apply prefilter before LLM call**

In `MatchHandler.__call__()`, after the `match_score is not None` short-circuit and before importing `matching_agent`, insert:

```python
        settings = get_settings()
        job = (
            await session.execute(select(Job).where(Job.id == app.job_id))
        ).scalar_one_or_none()
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.id == app.profile_id))
        ).scalar_one_or_none()
        if job is None or profile is None:
            await log.awarning(
                "worker.match.domain_missing",
                application_id=str(app.id),
                job_id=str(app.job_id),
                profile_id=str(app.profile_id),
                job_found=job is not None,
                profile_found=profile is not None,
            )
            return

        verdict = evaluate_remote_policy(profile, job)
        if verdict.hard_mismatch:
            gap = verdict.gap or "Deterministic match policy mismatch"
            app.match_score = _deterministic_rejection_score(
                settings.match_score_threshold
            )
            app.match_summary = (
                "Deterministic mismatch: recurring office attendance requirement"
            )
            app.match_rationale = gap
            app.match_strengths = []
            app.match_gaps = [gap]
            if app.status == "pending_review":
                app.status = "auto_rejected"
            session.add(app)
            await log.ainfo(
                "worker.match.prefilter_rejected",
                application_id=str(app.id),
                gap=gap,
                score=app.match_score,
            )
            return
```

Then remove the later duplicate `settings = get_settings()` line before the threshold check, because settings is now loaded before the prefilter.

- [ ] **Step 6: Run match handler tests**

Run:

```bash
uv run pytest tests/integration/test_handler_match.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/worker/handlers/match.py tests/integration/test_handler_match.py
git commit -m "feat(match): prefilter deterministic remote mismatches"
```

## Task 5: Full Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest tests/unit/test_worker_config.py tests/unit/test_remote_policy.py tests/integration/test_queue_service.py tests/integration/test_worker_lifecycle.py tests/integration/test_handler_match.py -v
```

Expected: PASS.

- [ ] **Step 2: Run lint**

Run:

```bash
uv run ruff check app/ tests/
```

Expected: PASS.

- [ ] **Step 3: Check for unintended lockfile changes**

Run:

```bash
git status --short
```

Expected: only intentional source/test files are modified. If `uv.lock` changed and no dependency was intentionally added, restore it before committing.

- [ ] **Step 4: Commit any verification-only cleanup**

If lint required formatting or import-order edits, commit those exact files:

```bash
git add app/ tests/
git commit -m "chore: tidy worker lane implementation"
```

If no files changed after verification, do not create a commit.
