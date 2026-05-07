# Frontend UX Redesign — Plan D: Analytics + Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the in-app event-log analytics layer (events table, ingest endpoint, frontend wrapper, instrumentation, SQL views) per spec section 7. Plus the deferred cleanup: delete `Onboarding.tsx`, `Applied.tsx`, `InvalidSlugsNotice.tsx`, the `/profile` and `/applied` route aliases. Plus a tiny backend extension so `pending_review` is accepted in the review PATCH (lets "Move back to pending" actually work).

**Architecture:** New `Event` SQLModel with JSONB properties + indices on `(profile_id, occurred_at)`, `(name, occurred_at)`, `(session_id, occurred_at)`. `POST /api/events` accepts a batched body, caps 50 per request, optional auth (anonymous events tied to client-generated `session_id`). Daily maintenance extends to delete events older than 90 days. Frontend `lib/track.ts` is a single-file wrapper: `track(name, properties?)` queues into a buffer, flushes every 5s and on `pagehide`. Calls are added at the surfaces in the spec's event canon. SQL views live in `scripts/analytics_views.sql`, applied once via `psql -f`.

**Tech Stack:** FastAPI + SQLModel + Alembic + Postgres + structlog + pytest (backend). React 18 + TS + Vitest (frontend). No new runtime deps.

**Spec:** `docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md` Section 7. Plus the cleanup deferred from Plans B/C.

**Branching:** `feat/analytics-cleanup`, branched from `main` after Plan C (PR #96) merged.

---

## File Structure

**Files to create (backend):**

```
app/models/event.py                                Event SQLModel (UUID id, profile_id FK, session_id, name, properties JSONB, occurred_at, user_agent, path)
alembic/versions/<hash>_add_events_table.py        Migration (table + indices)
app/api/events.py                                  POST /api/events ingest endpoint
tests/integration/test_events_api.py               Integration tests for the endpoint
scripts/analytics_views.sql                        Five views + setup SQL
```

**Files to create (frontend):**

```
frontend/src/lib/track.ts                          track() public function + buffered flush
frontend/src/lib/track.test.ts                     Tests for batching, flush, keepalive
```

**Files to modify (backend):**

```
app/models/__init__.py                             Register Event so alembic env.py sees it
app/scheduler/tasks.py                             run_daily_maintenance: delete events older than 90 days
app/api/applications.py                            PATCH endpoint accepts 'pending_review' in addition to 'dismissed'/'applied'
app/main.py                                        Register events router (if not auto-discovered)
tests/integration/test_applications_review.py     (or wherever current tests live) Add a test for pending_review acceptance
```

**Files to modify (frontend):**

```
frontend/src/App.tsx                               Remove /profile and /applied routes (and their imports); ensure no dead imports remain
frontend/src/api/client.ts                         Widen reviewApplication's status union to include 'pending_review'
frontend/src/pages/ApplicationReview.tsx           Drop the cast in moveBackToPending now that the API accepts pending_review
frontend/src/components/feed/MatchCard.tsx         Wire track('match.card_opened') etc. (instrumentation)
... (other instrumentation touchpoints, see Task 9)
```

**Files to delete:**

```
frontend/src/pages/Onboarding.tsx
frontend/src/pages/Onboarding.test.tsx
frontend/src/pages/Applied.tsx
frontend/src/components/InvalidSlugsNotice.tsx
```

---

## Task 0: Setup branch + baseline

**Files:** none

- [ ] **Step 1: Confirm clean tree, on `feat/analytics-cleanup` (already created)**

```bash
cd /Users/panibrat/dev/job-application-agent
git status
git log --oneline -3
```

Expected: clean tree, on `feat/analytics-cleanup`, parent main includes Plan C (`4a20d0a`).

- [ ] **Step 2: Capture baseline test counts**

```bash
cd frontend && npm install && npm run test 2>&1 | grep -E "Tests"
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ tests/e2e/ -q 2>&1 | tail -5
```

Expected: 194 frontend tests pass. Backend tests pass (note count). Plan D adds new tests, never deletes existing ones (other than tests of the files we delete in Tasks 6–8).

- [ ] **Step 3: Verify dev DB is reachable**

```bash
docker compose up -d db
until docker compose exec db pg_isready -U postgres > /dev/null 2>&1; do sleep 1; done
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  make migrate ARGS="upgrade head"
```

Expected: migrations clean.

---

## Task 1: Backend — `Event` model + Alembic migration

**Files:**
- Create: `app/models/event.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/<hash>_add_events_table.py` (via `make migrate ARGS="revision --autogenerate -m 'add events table'"`)

- [ ] **Step 1: Create the model**

Create `app/models/event.py`:

```python
"""Event log for in-app analytics — see spec section 7.

Authenticated events tie to profile_id; anonymous events tie to a
client-generated session_id (random UUID stored in sessionStorage).
Retention is 90 days, enforced by run_daily_maintenance."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    profile_id: UUID | None = Field(
        default=None,
        sa_column=Column(ForeignKey("user_profiles.id"), index=True, nullable=True),
    )
    session_id: str = Field(index=True, max_length=64)
    name: str = Field(index=True, max_length=64)
    properties: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )
    user_agent: str | None = Field(default=None, max_length=512)
    path: str | None = Field(default=None, max_length=256)
```

Why explicit `sa_column` for the FK and JSONB: per CLAUDE.md, SQLModel does NOT auto-detect ARRAY/JSONB nor mixed nullability on FKs.

- [ ] **Step 2: Register in `app/models/__init__.py`**

Find the existing model imports and add:

```python
from app.models.event import Event  # noqa: F401
```

(Alembic's `env.py` only sees models registered here.)

- [ ] **Step 3: Generate the migration**

```bash
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  make migrate ARGS="revision --autogenerate -m 'add events table for in-app analytics'"
```

Expected: a new file appears in `alembic/versions/`. Open it and inspect — should create `events` table with columns matching the model and three indices: `ix_events_profile_id`, `ix_events_name`, `ix_events_session_id`, `ix_events_occurred_at`. If the autogenerated migration is missing one of those, edit it manually to add the missing index.

The `Event` model implies these indices via `index=True` on each indexed column. Compound indices (`(profile_id, occurred_at)` etc., per spec) are NOT auto-generated. Manually add them by appending to the migration's `upgrade()` after the `create_table` call:

```python
op.create_index('ix_events_profile_id_occurred_at', 'events', ['profile_id', 'occurred_at'])
op.create_index('ix_events_name_occurred_at',       'events', ['name', 'occurred_at'])
op.create_index('ix_events_session_id_occurred_at', 'events', ['session_id', 'occurred_at'])
```

And mirror them in `downgrade()` with `op.drop_index(...)` calls.

- [ ] **Step 4: Apply the migration to local DB**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  make migrate ARGS="upgrade head"
```

Expected: clean apply. Verify the table exists:

```bash
docker compose exec db psql -U jobagent -d jobagent -c '\d events'
```

Expected: `events` table with columns and indices listed.

- [ ] **Step 5: Run existing backend test suite — confirm no regressions**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ tests/e2e/ -q
```

Expected: same pass count as Task 0 baseline (no new tests yet; the model is idle).

- [ ] **Step 6: Commit**

```bash
git add app/models/event.py app/models/__init__.py alembic/versions/*add_events*
git commit -m "feat(events): Event model + Alembic migration

In-app analytics layer per spec section 7. Single events table with
profile_id (nullable), session_id, name, JSONB properties, occurred_at,
user_agent, path. Compound indices (profile_id+occurred_at,
name+occurred_at, session_id+occurred_at) added manually in the
migration since autogenerate doesn't synthesize them."
```

---

## Task 2: Backend — `POST /api/events` ingest endpoint

**Files:**
- Create: `app/api/events.py`
- Create: `tests/integration/test_events_api.py`
- Modify: `app/main.py` (register router if it doesn't auto-discover)

- [ ] **Step 1: Failing test**

Create `tests/integration/test_events_api.py`:

```python
"""Integration tests for POST /api/events — the analytics ingest endpoint
defined in plan D. Validates batching, capping, optional auth, and
the 204 fire-and-forget contract."""

import pytest


@pytest.mark.asyncio
async def test_events_post_returns_204_for_authenticated_batch(test_app):
    """Authenticated client posts a batch; rows land tied to profile_id."""
    # Ensure dev profile exists
    await test_app.get("/api/profile")

    body = {
        "session_id": "sess-abc",
        "events": [
            {"name": "feed.viewed", "properties": {"status_filter": "pending"},
             "path": "/"},
            {"name": "match.card_opened", "properties": {"application_id": "x", "score": 0.87},
             "path": "/"},
        ],
    }
    r = await test_app.post("/api/events", json=body)
    assert r.status_code == 204

    # Verify rows landed via SELECT
    from app.database import get_session_factory
    from sqlmodel import select
    from app.models.event import Event

    async with get_session_factory()() as s:
        rows = (await s.execute(select(Event).where(Event.session_id == "sess-abc"))).scalars().all()
    assert len(rows) == 2
    names = {r.name for r in rows}
    assert names == {"feed.viewed", "match.card_opened"}
    assert all(r.profile_id is not None for r in rows)


@pytest.mark.asyncio
async def test_events_caps_batch_at_50(test_app):
    """A batch of 60 events ingests only the first 50; overflow is silently dropped."""
    await test_app.get("/api/profile")

    body = {
        "session_id": "sess-cap",
        "events": [{"name": f"test.event_{i}", "properties": None, "path": None} for i in range(60)],
    }
    r = await test_app.post("/api/events", json=body)
    assert r.status_code == 204

    from app.database import get_session_factory
    from sqlmodel import select, func
    from app.models.event import Event

    async with get_session_factory()() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(Event).where(Event.session_id == "sess-cap")
        )).scalar_one()
    assert cnt == 50, f"expected 50 rows after cap, got {cnt}"


@pytest.mark.asyncio
async def test_events_records_user_agent_and_path(test_app):
    """The endpoint extracts UA from request headers and path from each event."""
    await test_app.get("/api/profile")

    body = {
        "session_id": "sess-ua",
        "events": [{"name": "feed.viewed", "properties": None, "path": "/?status=applied"}],
    }
    r = await test_app.post(
        "/api/events", json=body, headers={"User-Agent": "TestAgent/1.0"}
    )
    assert r.status_code == 204

    from app.database import get_session_factory
    from sqlmodel import select
    from app.models.event import Event

    async with get_session_factory()() as s:
        row = (await s.execute(
            select(Event).where(Event.session_id == "sess-ua")
        )).scalar_one()
    assert "TestAgent" in (row.user_agent or "")
    assert row.path == "/?status=applied"
```

Run, expect FAIL (endpoint doesn't exist):

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/integration/test_events_api.py -v
```

- [ ] **Step 2: Implement the endpoint**

Create `app/api/events.py`:

```python
"""POST /api/events — analytics ingest. Fire-and-forget; returns 204.

Auth is optional: when present, events tie to profile_id; otherwise
they only carry session_id (anonymous). Batches are capped at 50 per
request — overflow is dropped silently. The client (lib/track.ts)
batches every 5s, so the cap should never be hit in practice."""

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile_optional
from app.database import get_db
from app.models.event import Event
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/events", tags=["events"])

MAX_EVENTS_PER_BATCH = 50


class EventIn(BaseModel):
    name: str
    properties: dict | None = None
    path: str | None = None


class EventBatchIn(BaseModel):
    session_id: str
    events: list[EventIn]


@router.post("", status_code=204)
async def log_events(
    body: EventBatchIn,
    request: Request,
    profile: UserProfile | None = Depends(get_current_profile_optional),
    session: AsyncSession = Depends(get_db),
):
    profile_id = profile.id if profile else None
    ua = (request.headers.get("user-agent") or "")[:512]

    rows = [
        Event(
            profile_id=profile_id,
            session_id=body.session_id[:64],
            name=ev.name[:64],
            properties=ev.properties,
            user_agent=ua,
            path=(ev.path or "")[:256] or None,
        )
        for ev in body.events[:MAX_EVENTS_PER_BATCH]
    ]
    if rows:
        session.add_all(rows)
        await session.commit()
    # 204: no body
```

The `get_current_profile_optional` dep doesn't exist yet — let me reuse the existing `get_current_profile` but make it not throw on missing auth. Inspect `app/api/deps.py`:

```bash
grep -nE "get_current_profile|require_auth" app/api/deps.py
```

If `get_current_profile_optional` doesn't exist, add it. Open `app/api/deps.py` and append (preserving existing `get_current_profile`):

```python
async def get_current_profile_optional(
    user: User | None = Depends(...),
    session: AsyncSession = Depends(get_db),
) -> UserProfile | None:
    """Like get_current_profile but returns None instead of 401 when unauthenticated.
    Used for the /api/events endpoint, which accepts anonymous events tied
    to a client-generated session_id only."""
    if user is None:
        return None
    # ... mirror the body of get_current_profile but skip the raise on missing
```

Implementation depends on the actual deps file. The simplest safe option if optional auth is hard:

**Alternative**: don't bother with optional auth — require auth on `POST /api/events`. Anonymous (pre-login) events would be lost. For a portfolio app with effectively one user, that's fine. Update the implementation to use `get_current_profile` directly. Spec says optional was preferred but isn't load-bearing.

Decide based on what's easy in deps.py. Document the choice in the commit message.

- [ ] **Step 3: Register the router**

Find where routers are registered in `app/main.py` (look for `app.include_router(...)` lines). Add:

```python
from app.api import events as events_api
app.include_router(events_api.router)
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/integration/test_events_api.py -v
```

Expected: 3 tests pass. If you went the "auth required" route, the tests still pass because `test_app` is authenticated as the dev user.

- [ ] **Step 5: Run full backend suite — confirm no regressions**

```bash
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ tests/e2e/ -q
```

- [ ] **Step 6: Commit**

```bash
git add app/api/events.py app/main.py app/api/deps.py tests/integration/test_events_api.py
git commit -m "feat(events): POST /api/events ingest endpoint

Batched ingest, cap 50 per request, 204 fire-and-forget. Auth status:
[either 'optional — anonymous events allowed' or 'required — anonymous
deferred', depending on deps.py]. UA truncated to 512, path to 256.
Spec section 7."
```

---

## Task 3: Backend — daily maintenance retention (90 days)

**Files:**
- Modify: `app/scheduler/tasks.py`
- Modify: existing maintenance test (or add a new one)

- [ ] **Step 1: Find the maintenance test**

```bash
grep -rn "run_daily_maintenance\|maintenance.applications_trimmed" tests/ | head -5
```

There's likely a test that exercises `run_daily_maintenance`. Add a step there (or in a new test) to:
1. Insert an old event (`occurred_at = now() - 100 days`)
2. Insert a recent event (`occurred_at = now() - 30 days`)
3. Run maintenance
4. Assert old event was deleted, recent event survived

If no good place exists, create `tests/integration/test_maintenance_events_retention.py` with a focused test.

- [ ] **Step 2: Add the failing test**

```python
"""Daily maintenance deletes events older than 90 days (spec section 7)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models.event import Event
from app.scheduler.tasks import run_daily_maintenance


@pytest.mark.asyncio
async def test_maintenance_deletes_events_older_than_90_days(db_session):
    now = datetime.now(UTC)
    old = Event(session_id="old", name="x", occurred_at=now - timedelta(days=100))
    fresh = Event(session_id="fresh", name="x", occurred_at=now - timedelta(days=30))
    db_session.add_all([old, fresh])
    await db_session.commit()

    await run_daily_maintenance()

    rows = (await db_session.execute(select(Event))).scalars().all()
    names = {r.session_id for r in rows}
    assert "fresh" in names
    assert "old" not in names
```

(Adjust the fixture name `db_session` to whatever the existing tests use. Look at neighboring test files.)

Run, expect FAIL.

- [ ] **Step 3: Implement**

In `app/scheduler/tasks.py`, find `run_daily_maintenance()`. After the existing `applications_trimmed` block (around line 165), add:

```python
        # Events retention — delete > 90 days old (spec section 7).
        from app.models.event import Event
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(days=90)
        events_result = await session.execute(
            text("DELETE FROM events WHERE occurred_at < :cutoff"),
            {"cutoff": cutoff},
        )
        await session.commit()
        events_deleted = events_result.rowcount
        if events_deleted:
            await log.ainfo("maintenance.events_deleted", count=events_deleted)
```

Update the return dict to include `events_deleted`.

- [ ] **Step 4: Run test, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/tasks.py tests/integration/test_maintenance_events_retention.py
git commit -m "feat(events): retention — daily maintenance deletes events > 90 days

Spec section 7. Bounded growth even at sustained ingest rates."
```

---

## Task 4: Backend — accept `pending_review` in review PATCH

**Files:**
- Modify: `app/api/applications.py`
- Modify: `tests/integration/test_apply_lifecycle.py` (or wherever review PATCH tests live; if none, add to a closest neighbor)

- [ ] **Step 1: Failing test**

Find the review-PATCH tests:

```bash
grep -rn "reviewApplication\|patch.*applications\|status.*dismissed" tests/ | head -10
```

Add a test that PATCHes with `status=pending_review` and asserts the row's status is now `pending_review`. If no test file currently has these PATCHes, the closest one is `tests/integration/test_apply_lifecycle.py`. Add:

```python
@pytest.mark.asyncio
async def test_review_patch_accepts_pending_review_for_undo(test_app, applied_application):
    """Plan D: PATCH accepts pending_review so the UI's 'Move back to pending'
    action can roll back an accidental Open posting click."""
    r = await test_app.patch(
        f"/api/applications/{applied_application.id}",
        json={"status": "pending_review"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending_review"
```

(Adapt fixture `applied_application` to whatever the existing tests use; if no such fixture exists, create a minimal one inline.)

Run, expect FAIL (currently 400).

- [ ] **Step 2: Implement**

In `app/api/applications.py`, lines around 135–142, change:

```python
    action = data.get("status")
    if action not in ("dismissed", "applied"):
        raise HTTPException(status_code=400, detail="status must be dismissed or applied")

    if action == "applied" and app.status != "applied":
        app.applied_at = datetime.now(UTC)
    app.status = action
```

to:

```python
    action = data.get("status")
    if action not in ("dismissed", "applied", "pending_review"):
        raise HTTPException(status_code=400, detail="status must be dismissed, applied, or pending_review")

    if action == "applied" and app.status != "applied":
        app.applied_at = datetime.now(UTC)
    if action == "pending_review":
        # Undo path: clear applied_at so the UI's "applied" status doesn't linger.
        app.applied_at = None
    app.status = action
```

- [ ] **Step 3: Run test, expect PASS**

- [ ] **Step 4: Commit**

```bash
git add app/api/applications.py tests/integration/test_apply_lifecycle.py
git commit -m "feat(applications): review PATCH accepts pending_review

Lets the frontend's 'Move back to pending' kebab action roll back an
accidental Open posting click. applied_at is cleared on the transition
so the UI no longer shows '✓ Applied'."
```

---

## Task 5: Frontend — `track()` wrapper

**Files:**
- Create: `frontend/src/lib/track.ts`
- Create: `frontend/src/lib/track.test.ts`

- [ ] **Step 1: Failing test**

Create `frontend/src/lib/track.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// We import dynamically inside each test so module-level state (queue, sessionId)
// can be reset between tests.

function resetModule() {
  vi.resetModules()
  sessionStorage.clear()
}

describe('track()', () => {
  let originalFetch: typeof fetch
  beforeEach(() => {
    resetModule()
    vi.useFakeTimers()
    originalFetch = globalThis.fetch
  })
  afterEach(() => {
    vi.useRealTimers()
    globalThis.fetch = originalFetch
  })

  it('does not fetch synchronously — flushes after the timer', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    track('feed.viewed', { status_filter: 'pending' })
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('flushes the queue after 5 seconds', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    track('feed.viewed')
    track('match.card_opened', { application_id: 'a1' })
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const init = fetchSpy.mock.calls[0][1] as RequestInit
    const body = JSON.parse(init.body as string)
    expect(body.events).toHaveLength(2)
    expect(body.events[0].name).toBe('feed.viewed')
    expect(body.events[1].name).toBe('match.card_opened')
    expect(typeof body.session_id).toBe('string')
  })

  it('flushes on pagehide', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    track('app.error_boundary_hit')
    window.dispatchEvent(new Event('pagehide'))
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('caps each batch at 50 events', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    for (let i = 0; i < 60; i++) track(`evt_${i}`)
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string)
    expect(body.events).toHaveLength(50)
  })

  it('swallows fetch errors silently', async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error('network'))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    track('feed.viewed')
    // Should not throw
    await expect(vi.advanceTimersByTimeAsync(5_000)).resolves.toBeUndefined()
  })

  it('persists session_id across calls', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track } = await import('./track')
    track('a')
    await vi.advanceTimersByTimeAsync(5_000)
    track('b')
    await vi.advanceTimersByTimeAsync(5_000)
    const sid1 = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string).session_id
    const sid2 = JSON.parse((fetchSpy.mock.calls[1][1] as RequestInit).body as string).session_id
    expect(sid1).toBe(sid2)
  })
})
```

Run, expect FAIL.

- [ ] **Step 2: Implement**

Create `frontend/src/lib/track.ts`:

```ts
/** In-app event tracking — see spec section 7.
 *
 *  Public API: `track(name, properties?)`. Calls are buffered and flushed
 *  every 5s and on `pagehide`. Failures are swallowed so analytics never
 *  break the app. */

interface EventIn {
  name: string
  properties?: Record<string, unknown>
  path?: string
}

const SESSION_KEY = 'ja_session_id'
const FLUSH_MS = 5_000
const MAX_BATCH = 50

const queue: EventIn[] = []
let flushTimer: number | null = null

function getSessionId(): string {
  let s = sessionStorage.getItem(SESSION_KEY)
  if (!s) {
    s = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36)
    sessionStorage.setItem(SESSION_KEY, s)
  }
  return s
}

async function flush(): Promise<void> {
  flushTimer = null
  if (queue.length === 0) return
  const batch = queue.splice(0, MAX_BATCH)
  // If queue still has items (overflow beyond MAX_BATCH), schedule another flush.
  if (queue.length > 0 && flushTimer == null) {
    flushTimer = window.setTimeout(flush, FLUSH_MS)
  }
  try {
    await fetch('/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: getSessionId(), events: batch }),
      keepalive: true,
    })
  } catch {
    // Swallow — analytics MUST NOT break the app.
  }
}

export function track(name: string, properties?: Record<string, unknown>): void {
  queue.push({
    name,
    properties,
    path: typeof window !== 'undefined' ? window.location.pathname + window.location.search : undefined,
  })
  if (flushTimer == null && typeof window !== 'undefined') {
    flushTimer = window.setTimeout(flush, FLUSH_MS)
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { void flush() })
}
```

- [ ] **Step 3: Run tests, expect 6 PASS**

```bash
cd frontend && npx vitest run src/lib/track.test.ts
```

If a test fails because `crypto.randomUUID` isn't available in jsdom, the fallback branch handles it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/track.ts frontend/src/lib/track.test.ts
git commit -m "feat(frontend/track): track() event log wrapper

Single-file wrapper for the events ingest endpoint. Buffered, flushes
every 5s and on pagehide. Cap of 50 per batch matches the backend.
session_id stored in sessionStorage. fetch errors silently swallowed
so analytics never break the app."
```

After commit: 200 frontend tests (194 + 6).

---

## Task 6: Frontend — instrument the spec's event canon

**Files:** modifications across the frontend touchpoints.

This task wires `track()` calls into the surfaces specified in spec section 7. Each call is one-line and additive — does not change any UI behavior. Tests verify only that nothing breaks (the existing tests catch regressions; we do NOT add per-call tests since each is trivial).

### 6.1 `auth.signin_clicked` / `auth.signin_succeeded` / `auth.signin_failed`

In `frontend/src/pages/Landing.tsx`:

```ts
import { track } from '../lib/track'

// Inside startGoogleLogin, before the fetch:
track('auth.signin_clicked', { method: 'google' })

// In the success path (just before window.location.href = ...):
track('auth.signin_succeeded', { method: 'google' })

// In the catch:
track('auth.signin_failed', { method: 'google', reason: String(err) })

// Same for startDevLogin with method: 'dev'.
```

### 6.2 `auth.signed_out`

In `frontend/src/context/AuthContext.tsx`, inside `signOut()`:

```ts
import { track } from '../lib/track'
// At top of signOut, before sessionStorage.removeItem:
track('auth.signed_out')
```

### 6.3 `feed.viewed`, `feed.status_filter_changed`, `feed.sync_clicked`, `feed.sync_succeeded`, `feed.sync_failed`, `feed.empty_state_shown`

In `frontend/src/pages/Matches.tsx`, on mount, inside the component (using `useEffect`):

```ts
import { useEffect } from 'react'
import { track } from '../lib/track'

useEffect(() => {
  track('feed.viewed', {
    status_filter: status,
    count_pending: counts.pending,
    count_applied: counts.applied,
    count_dismissed: counts.dismissed,
  })
}, [status])
```

In `frontend/src/lib/useStatusFilter.ts`, inside `setStatus`:

```ts
import { track } from './track'

// before setParams:
track('feed.status_filter_changed', { from: status, to: next })
```

In `frontend/src/components/feed/SyncRow.tsx`:

```ts
import { track } from '../../lib/track'

// Inside the click handler before sync.mutate():
track('feed.sync_clicked', { source: 'feed_button' })

// In sync's onSuccess:
track('feed.sync_succeeded', { matched_now: data.matched_now ?? 0, queued_slugs: data.queued_slugs?.length ?? 0 })

// In sync's onError:
track('feed.sync_failed', { error: String(err) })
```

In the empty state branch of `Matches.tsx`:

```ts
// Wrap the empty-state JSX in a useEffect that fires once when it mounts:
useEffect(() => {
  track('feed.empty_state_shown', { reason: 'no_matches' })
}, [])
```

(Or fold this into the main `feed.viewed` effect with a flag.)

### 6.4 `match.card_opened` / `match.dismissed` / `match.applied` / `match.original_posting_opened` / `match.unapplied`

In `frontend/src/components/feed/MatchCard.tsx`:

```ts
import { track } from '../../lib/track'

// On the Card link click — but anchors don't have onClick that fires before nav.
// Use an onClick that runs synchronously before router takes over:
// (the Card primitive forwards onClick; pass it through)

<Card as="rrlink" to={...} onClick={() => track('match.card_opened', { application_id: app.id, score: app.match_score })}>
```

If `Card` doesn't currently forward `onClick` to the underlying element, that's a tiny extension to `frontend/src/components/ui/Card.tsx` — verify and adjust.

In the dismiss mutations (MatchCard, ApplicationReview, StickyActions):

```ts
// In useMutation onMutate or before mutate:
track('match.dismissed', { application_id: app.id, source: 'kebab' })  // or 'swipe' / 'detail_skip'
```

In `StickyActions.tsx`, inside `onOpenAndMark`:

```ts
track('match.original_posting_opened', { application_id: appId })
if (status === 'pending_review') {
  track('match.applied', { application_id: appId })
  markApplied.mutate()
}
```

In `ApplicationReview.tsx`, inside `moveBackToPending`:

```ts
track('match.unapplied', { application_id: id })
```

### 6.5 `cover_letter.*`

In `frontend/src/components/match-detail/CoverLetterEditor.tsx`:

```ts
import { track } from '../../lib/track'

// On Generate click:
track('cover_letter.generation_clicked', { application_id: appId })

// In generate.onSuccess:
track('cover_letter.generation_succeeded', { application_id: appId, model: doc.generation_model })

// In generate.onError:
track('cover_letter.generation_failed', { application_id: appId, reason: String(e) })

// In save.onSuccess:
track('cover_letter.saved', { application_id: appId, content_length: content.length })

// On the PDF download anchor click:
onClick={() => track('cover_letter.pdf_downloaded', { application_id: appId })}

// First-keystroke detection — track once per session per doc:
const [editedTracked, setEditedTracked] = useState(false)
// in onChange:
if (!editedTracked) {
  setEditedTracked(true)
  track('cover_letter.edited', { application_id: appId })
}
```

### 6.6 `coach.*`

In `frontend/src/components/coach/CoachDrawer.tsx`:

```ts
import { useEffect } from 'react'
import { track } from '../../lib/track'

useEffect(() => {
  if (open) {
    track('coach.opened', { source: 'deep_link', prompt_slug: slug ?? null })
  }
}, [open])
```

In `frontend/src/components/coach/Coach.tsx`:

```ts
import { track } from '../../lib/track'

// In send(), at the start:
track('coach.message_sent', { length: text.length })

// Where the inline Search now CTA is rendered — on click:
onClick={() => { track('coach.search_now_clicked', { from_message_index: i }); triggerSync.mutate() }}

// On error inside the sendMessage onError callback:
track('coach.message_failed', { reason: 'stream_error' })
```

### 6.7 `settings.*`

In `frontend/src/pages/Settings.tsx`:

```ts
import { useEffect } from 'react'
import { track } from '../lib/track'

useEffect(() => { track('settings.viewed') }, [])
```

In `frontend/src/components/settings/SearchToggleSection.tsx`:

```ts
// In the toggle's onClick (or in toggle.onSuccess):
track('settings.search_toggled', { to: active ? 'paused' : 'active' })
```

In `frontend/src/components/settings/ResumeSection.tsx`:

```ts
// In upload.onSuccess:
track('settings.resume_uploaded', { extraction_status: result.extraction_status })
```

In `frontend/src/components/settings/TargetSlugsSection.tsx`:

```ts
// In add() before mutate:
track('settings.slug_added', { provider: key })
// In remove():
track('settings.slug_removed', { provider: key })
```

### 6.8 `profile.*`

In `frontend/src/components/feed/ProfileCompletenessCard.tsx`:

```ts
import { useEffect } from 'react'
import { track } from '../../lib/track'

useEffect(() => {
  const checksDone = items.filter(c => c.done).length
  track('profile.completeness_viewed', {
    checks_done: checksDone, checks_total: items.length, paused,
  })
}, [items, paused])

// On the "Tell coach →" Link click — convert Link to button or wrap with onClick:
// (or capture the click via the parent)
```

In `SyncRow.tsx`, when the first sync fires after a profile-update (this is the "Profile ready · Start search" scenario), tracker `profile.first_sync_started` instead of (or in addition to) `feed.sync_clicked`. The simplest implementation: don't bother distinguishing — `feed.sync_clicked` is sufficient telemetry; mark `profile.first_sync_started` as deferred unless the analyst really wants it (it can be derived from the funnel view: "first feed.sync_clicked per profile").

### 6.9 `app.error_boundary_hit`

We don't have an ErrorBoundary today. Skipping — out of scope unless the user adds one.

### Verification

- [ ] **Step 1: After all instrumentation is in place, run the full test suite**

```bash
npm run test && npx tsc --noEmit
```

Expected: 200 tests pass (no test additions in this task — we don't unit-test individual `track()` calls; the wrapper is tested in Task 5).

- [ ] **Step 2: Manual smoke** — start dev server, click around, check Network tab for periodic `POST /api/events` requests with reasonable bodies.

- [ ] **Step 3: Commit**

```bash
git add -A frontend/src
git commit -m "feat(frontend): instrument the spec's event canon

Adds track() calls at the surfaces named in spec section 7: auth, feed,
match, cover_letter, coach, settings, profile.completeness_viewed.

Each call is one line, additive — no UI behavior change. ErrorBoundary
event deferred (we don't have one today)."
```

---

## Task 7: Cleanup — delete `Onboarding.tsx` and `/profile` route

**Files:**
- Delete: `frontend/src/pages/Onboarding.tsx`
- Delete: `frontend/src/pages/Onboarding.test.tsx`
- Modify: `frontend/src/App.tsx` (remove `/profile` route + import)

- [ ] **Step 1: Verify no remaining imports of Onboarding outside App.tsx**

```bash
grep -rE "from .*pages/Onboarding'" frontend/src/ | grep -v App.tsx | grep -v Onboarding.test.tsx
```

Expected: empty. Coach drawer (Plan C) supersedes Onboarding-as-page; the legacy route was kept only as a transitional alias.

- [ ] **Step 2: Update App.tsx**

Remove the `import Onboarding from ...` line and the `/profile` Route. Final routes block:

```tsx
<Route path="/" element={<RequireAuth><Matches /></RequireAuth>} />
<Route path="/login" element={<Landing />} />
<Route path="/auth/callback" element={<AuthCallback />} />
<Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
<Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
<Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
<Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
```

(Keep `/applied` for now — Task 8 deletes it separately.)

- [ ] **Step 3: Delete the files**

```bash
rm frontend/src/pages/Onboarding.tsx frontend/src/pages/Onboarding.test.tsx
```

- [ ] **Step 4: Run frontend tests**

```bash
cd frontend && npm run test
```

Expected: pass count drops by however many tests Onboarding.test.tsx had. Note the new count.

- [ ] **Step 5: Update e2e**

The `tests/e2e/onboarding.spec.ts` (Playwright) tests the old `/profile` page. With `/profile` gone, those tests will 404. Either:
- (a) Delete the spec file entirely (its concerns are covered by the Coach drawer, which has its own component tests).
- (b) Rewrite the spec to drive the Coach drawer instead.

Option (a) is the right call — Coach is unit-tested; e2e for the drawer would be net-new scope. Delete the spec.

```bash
rm frontend/e2e/onboarding.spec.ts
```

- [ ] **Step 6: Run e2e — confirm no other regressions**

```bash
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  GOOGLE_API_KEY=test-key ENVIRONMENT=development \
  npm --prefix frontend run test:e2e
```

Expected: ~12 e2e tests pass (was 15; we deleted 3 onboarding specs).

- [ ] **Step 7: Commit**

```bash
git add -A frontend/src/pages/Onboarding.tsx frontend/src/pages/Onboarding.test.tsx \
        frontend/src/App.tsx frontend/e2e/onboarding.spec.ts
git commit -m "chore(frontend): delete Onboarding.tsx + /profile route + onboarding e2e

Coach drawer (Plan C) supersedes the chat-only Onboarding page. The
/profile alias was kept transitionally; users now reach the chat via
?coach=1 (drawer) or /settings (structured controls + Open Coach CTA).
Onboarding e2e specs deleted — Coach has its own unit tests."
```

---

## Task 8: Cleanup — delete `Applied.tsx` and `/applied` route

**Files:**
- Delete: `frontend/src/pages/Applied.tsx`
- Modify: `frontend/src/App.tsx` (remove `/applied` route + import)

- [ ] **Step 1: Verify no Applied imports outside App.tsx**

```bash
grep -rE "from .*pages/Applied'" frontend/src/ | grep -v App.tsx
```

Expected: empty. The Feed's status chips (`?status=applied` / `?status=dismissed`) supersede the `/applied` page.

- [ ] **Step 2: Update App.tsx — remove `/applied` Route + Applied import**

- [ ] **Step 3: Delete the file**

```bash
rm frontend/src/pages/Applied.tsx
```

- [ ] **Step 4: Update e2e**

`tests/e2e/auth-and-nav.spec.ts` has a "History page loads without crashing" test that hits `/applied`. With the route gone, it'll fail. Update or delete:
- The test goes to `/applied` and expects a "History" heading. Since `/applied` no longer exists, the route would land on the `*` fallback (probably re-rendering Matches via the index route, depending on router config). Cleanest: delete that test.

In `frontend/e2e/auth-and-nav.spec.ts`, find and delete the entire `test('History page loads without crashing the app', ...)` block.

- [ ] **Step 5: Run tests + e2e**

```bash
cd frontend && npm run test
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  GOOGLE_API_KEY=test-key ENVIRONMENT=development \
  npm --prefix frontend run test:e2e
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src/pages/Applied.tsx frontend/src/App.tsx frontend/e2e/auth-and-nav.spec.ts
git commit -m "chore(frontend): delete Applied.tsx + /applied route + History e2e

The Feed's status chips (?status=applied / ?status=dismissed) replace
the /applied page. e2e History test removed — its concern is folded
into the Feed e2e."
```

---

## Task 9: Cleanup — delete `InvalidSlugsNotice.tsx`

**Files:**
- Delete: `frontend/src/components/InvalidSlugsNotice.tsx`

- [ ] **Step 1: Verify no remaining imports**

```bash
grep -rE "from .*components/InvalidSlugsNotice'" frontend/src/
```

Expected: empty. PrunedSlugsSection (Plan C) supersedes its content on the new Settings page.

- [ ] **Step 2: Delete + verify tests pass**

```bash
rm frontend/src/components/InvalidSlugsNotice.tsx
cd frontend && npm run test
```

- [ ] **Step 3: Commit**

```bash
git add -A frontend/src/components/InvalidSlugsNotice.tsx
git commit -m "chore(frontend): delete InvalidSlugsNotice.tsx

Replaced by PrunedSlugsSection on the Settings page (Plan C). No
remaining consumers."
```

---

## Task 10: SQL views for analytics

**Files:**
- Create: `scripts/analytics_views.sql`
- Modify: `Makefile` or document `apply-analytics-views` invocation (optional)

- [ ] **Step 1: Create the SQL file**

Create `scripts/analytics_views.sql`:

```sql
-- Analytics views for the in-app event log (spec section 7).
--
-- Apply once with:
--   psql $DATABASE_URL -f scripts/analytics_views.sql
--
-- Re-runnable: every view uses CREATE OR REPLACE.

-- 1. Onboarding funnel: first time per profile that each milestone fired.
CREATE OR REPLACE VIEW analytics_onboarding_funnel AS
WITH steps AS (
  SELECT
    profile_id,
    MIN(occurred_at) FILTER (WHERE name = 'auth.signin_succeeded')          AS signed_in_at,
    MIN(occurred_at) FILTER (WHERE name = 'profile.coach_opened_from_card') AS coach_first_open,
    MIN(occurred_at) FILTER (WHERE name = 'feed.sync_clicked')              AS first_sync_at,
    MIN(occurred_at) FILTER (WHERE name = 'match.card_opened')              AS first_match_open,
    MIN(occurred_at) FILTER (WHERE name = 'match.applied')                  AS first_apply_at
  FROM events
  GROUP BY profile_id
)
SELECT * FROM steps WHERE profile_id IS NOT NULL;

-- 2. Per-event usage in the trailing 30 days.
CREATE OR REPLACE VIEW analytics_feature_usage_30d AS
SELECT
  name,
  count(*)                       AS occurrences,
  count(DISTINCT session_id)     AS sessions,
  count(DISTINCT profile_id)     AS profiles
FROM events
WHERE occurred_at > now() - interval '30 days'
GROUP BY name
ORDER BY occurrences DESC;

-- 3. Match-dismiss patterns by source and score.
CREATE OR REPLACE VIEW analytics_dismiss_patterns AS
SELECT
  properties->>'source' AS source,
  ROUND((properties->>'score')::numeric, 2) AS score,
  count(*) AS dismissals
FROM events
WHERE name = 'match.dismissed'
  AND occurred_at > now() - interval '30 days'
GROUP BY source, score
ORDER BY dismissals DESC;

-- 4. Cover-letter funnel per application.
CREATE OR REPLACE VIEW analytics_cover_letter_funnel AS
WITH per_app AS (
  SELECT
    (properties->>'application_id')::uuid AS application_id,
    MAX(CASE WHEN name = 'cover_letter.generation_clicked'  THEN 1 ELSE 0 END) AS clicked,
    MAX(CASE WHEN name = 'cover_letter.generation_succeeded' THEN 1 ELSE 0 END) AS succeeded,
    MAX(CASE WHEN name = 'cover_letter.edited'              THEN 1 ELSE 0 END) AS edited,
    MAX(CASE WHEN name = 'cover_letter.pdf_downloaded'      THEN 1 ELSE 0 END) AS downloaded,
    MAX(CASE WHEN name = 'match.applied'                    THEN 1 ELSE 0 END) AS applied
  FROM events
  WHERE name IN (
    'cover_letter.generation_clicked',
    'cover_letter.generation_succeeded',
    'cover_letter.edited',
    'cover_letter.pdf_downloaded',
    'match.applied'
  )
  AND properties ? 'application_id'
  GROUP BY application_id
)
SELECT
  count(*)                   AS apps_with_activity,
  sum(clicked)               AS clicked,
  sum(succeeded)             AS succeeded,
  sum(edited)                AS edited,
  sum(downloaded)            AS downloaded,
  sum(applied)               AS applied
FROM per_app;

-- 5. Sync friction over the trailing 30 days, daily.
CREATE OR REPLACE VIEW analytics_sync_friction_30d AS
SELECT
  date_trunc('day', occurred_at) AS day,
  sum(CASE WHEN name = 'feed.sync_clicked'    THEN 1 ELSE 0 END) AS clicks,
  sum(CASE WHEN name = 'feed.sync_succeeded'  THEN 1 ELSE 0 END) AS successes,
  sum(CASE WHEN name = 'feed.sync_failed'     THEN 1 ELSE 0 END) AS failures
FROM events
WHERE occurred_at > now() - interval '30 days'
GROUP BY 1
ORDER BY 1;
```

- [ ] **Step 2: Apply against the local DB and sanity-check the views**

```bash
docker compose exec -T db psql -U jobagent -d jobagent < scripts/analytics_views.sql
docker compose exec db psql -U jobagent -d jobagent -c "SELECT * FROM analytics_feature_usage_30d LIMIT 5;"
```

Expected: views are created without errors and return rows (will be empty until events accumulate).

- [ ] **Step 3: Commit**

```bash
git add scripts/analytics_views.sql
git commit -m "feat(events/views): analytics views (onboarding funnel, usage, etc.)

Re-runnable SQL (CREATE OR REPLACE) for the five views named in spec
section 7. Apply once with: psql \$DATABASE_URL -f scripts/analytics_views.sql"
```

---

## Task 11: Final verification + PR

- [ ] **Step 1: Full test sweep**

```bash
cd frontend && npm run test && npx tsc --noEmit && npm run build
cd /Users/panibrat/dev/job-application-agent
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  uv run pytest tests/unit/ tests/integration/ tests/e2e/ -q
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  GOOGLE_API_KEY=test-key ENVIRONMENT=development \
  npm --prefix frontend run test:e2e
```

Expected: all green.

- [ ] **Step 2: Push + open PR**

```bash
git push -u origin feat/analytics-cleanup
gh pr create --title "feat(frontend+backend): UX redesign Plan D — analytics + cleanup" --body "$(cat <<'EOF'
## Summary
Final plan in the redesign series. Per spec section 7 (analytics) plus the deferred cleanup from Plans B/C.

**Backend:**
- New \`events\` table + Alembic migration (UUID id, profile_id FK, session_id, name, JSONB properties, occurred_at, user_agent, path; compound indices on (profile_id, occurred_at), (name, occurred_at), (session_id, occurred_at)).
- \`POST /api/events\` — batched ingest, cap 50, 204 fire-and-forget.
- \`run_daily_maintenance\` extended: deletes events older than 90 days.
- \`PATCH /api/applications/:id\` accepts \`pending_review\` (lets the UI's "Move back to pending" actually work).

**Frontend:**
- \`lib/track.ts\` — buffered fire-and-forget event tracking. Flushes every 5s and on \`pagehide\`. Cap 50 per batch matches backend.
- Instrumented surfaces per the spec's event canon: auth, feed, match, cover_letter, coach, settings, profile.completeness_viewed.

**Cleanup:**
- Deleted \`Onboarding.tsx\`, \`Applied.tsx\`, \`InvalidSlugsNotice.tsx\` and removed \`/profile\`, \`/applied\` route aliases.
- Deleted \`tests/e2e/onboarding.spec.ts\` and the History e2e test (concerns folded into the Coach drawer / Feed coverage).

**SQL views:**
- \`scripts/analytics_views.sql\` — five views: onboarding funnel, feature usage 30d, dismiss patterns, cover letter funnel, sync friction 30d.

## Test plan
- [ ] CI green (frontend test, tsc, build, backend pytest)
- [ ] CI green for e2e
- [ ] Manual: open dev tools network tab; periodic POST /api/events with reasonable batch bodies after clicking around
- [ ] Manual: click Open posting → kebab → Move back to pending; status reverts cleanly
- [ ] Manual: \`psql \$DATABASE_URL -f scripts/analytics_views.sql\` applies; \`SELECT * FROM analytics_feature_usage_30d\` returns rows after using the app

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Watch CI**

If failures, fix on this branch (NEW commits, never amend) and push.

---

## Self-Review Checklist

- [ ] Spec coverage:
  - Events table ✓ + retention ✓ + ingest endpoint ✓ + frontend wrapper ✓ + instrumentation ✓ + SQL views ✓.
  - All event names in the spec canon are emitted somewhere (auth.*, feed.*, match.*, cover_letter.*, coach.*, settings.*, profile.completeness_viewed). \`app.error_boundary_hit\` is OUT (no boundary today). \`profile.first_sync_started\` is OUT (deferred — derivable from the funnel view).
- [ ] Cleanup: Onboarding, Applied, InvalidSlugsNotice deleted; /profile and /applied routes gone.
- [ ] Backend extension: pending_review accepted; "Move back to pending" path no longer error-toasts.
- [ ] No placeholders / TBDs.
- [ ] Type consistency: \`Event\`, \`EventIn\`, \`EventBatchIn\`, \`track()\` shapes consistent across backend / frontend.
- [ ] Tests written before implementation in every component task.

## Out of scope

- Per-instrumentation unit tests (the `track()` wrapper is tested; individual call sites would be over-tested).
- React `ErrorBoundary` + the `app.error_boundary_hit` event (no boundary in the app today).
- Light theme (still deferred from Plan A).
- Pull-to-refresh on the Feed (still deferred).

End of Plan D — and end of the redesign series.
