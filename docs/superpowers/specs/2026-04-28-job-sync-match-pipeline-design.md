# Job Sync & Match Pipeline — Design

**Status:** Draft
**Owner:** maksym@panibrat.com
**Date:** 2026-04-28

## Problem

`POST /api/jobs/sync` blocks the HTTP request for the entire sync + match cycle. On 2026-04-29 02:12 UTC a single click ran for **299.978s** and was killed by Cloud Run's 300s request timeout, returning **504**. The same click also failed silently to score the (partially) fetched jobs, because the post-response `BackgroundTasks.add_task(_score_after_sync, ...)` (`app/api/jobs.py:39`) does not reliably run on Cloud Run after the response is sent.

The serial design is wasteful in three independent ways:

1. **Per-user fetch.** When user A and user B both have slug `airbnb`, each profile's sync re-pulls the same Greenhouse board. Slug-level dedup of the *fetch* doesn't exist.
2. **Per-user matching against the global pool.** `match_service.score_and_match` (`app/services/match_service.py:115`) scores every active job against the profile, ignoring whether the company is in the user's `target_company_slugs`. Latent cost amplifier as the global pool grows.
3. **Invalid slug pollution.** A profile may permanently contain dead slugs (e.g. `openai`, `snowflake`, `slack` — 10 of them in tonight's failed sync) that 404 every cycle forever, with no auto-pruning.

## Goals

- "Sync now" returns within ~1s, never hits the Cloud Run wall, and feels instant by scoring against already-cached jobs immediately.
- Each Greenhouse slug is fetched at most once per **6h** across the entire system.
- Each job is scored against a profile at most once.
- Matching is **strictly slug-scoped** to the user's `target_company_slugs.greenhouse`.
- Invalid slugs are auto-pruned without manual intervention.
- Empty-slug users get a curated 5-slug seed so the dashboard isn't empty.
- Long-running work (≥5 min) drains across multiple cron ticks; no single HTTP request exceeds Cloud Run's 300s timeout.

## Non-goals

- Multi-source sourcing. Greenhouse-only stays (`PR 2/6: collapse to Greenhouse-only sourcing` is the standing decision).
- Cross-user job *visibility* changes. Application rows remain per-(profile, job).
- Cloud Tasks / Pub/Sub. We use the existing GHA cron + DB-backed queue pattern (already proven for `run_generation_queue`).
- Replacing the LangGraph matching agent. We re-use it; we just change *what we feed it* and *when*.

## Architecture

Two DB-backed queues drained by cron, plus an instant-feedback path on "Sync now":

```
                      ┌─────────────────────┐
  POST /api/jobs/sync─┤ enqueue stale slugs │──202 (queued summary)
                      │ kick instant match  │
                      └──────────┬──────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │ instant match (in-request)   │
                  │ scope: profile.slugs ∩       │
                  │   already-cached unmatched   │
                  │ cap: matching_jobs_per_batch │
                  └──────────────────────────────┘

  cron(*/15 min, GHA)  ─►  POST /internal/cron/process-sync-queue  ─►  fetch slugs (concurrent, 8-way)
  cron(*/15 min, GHA)  ─►  POST /internal/cron/process-match-queue ─►  drain pending_match apps (≤30/tick)
  cron(every 6h)       ─►  POST /internal/cron/sync                 ─►  enqueue all stale slugs across all active profiles
  cron(daily 03:00)    ─►  POST /internal/cron/maintenance          ─►  21d staleness + auto-pause + prune
```

## Data model changes

### New table: `slug_fetches`

Slug-level fetch state. Independent of any single user — this is the global cache key.

```python
class SlugFetch(SQLModel, table=True):
    __tablename__ = "slug_fetches"
    source: str = Field(primary_key=True)                  # "greenhouse_board"
    slug: str = Field(primary_key=True)
    last_fetched_at: datetime | None
    last_attempted_at: datetime | None
    last_status: str | None                                # "ok" | "invalid" | "transient_error"
    consecutive_404_count: int = 0
    consecutive_5xx_count: int = 0
    is_invalid: bool = False                               # set after 2 consecutive 404s
    invalid_reason: str | None
    queued_at: datetime | None                             # set when added to fetch queue, cleared on completion
```

- **Composite PK** `(source, slug)` so we can extend beyond Greenhouse later without column gymnastics.
- **`is_invalid=True`** is terminal — no further fetches; surfaced to user via `/api/sync/status` so they can remove the slug.
- **`queued_at`** is the queue marker. The cron worker selects rows where `queued_at IS NOT NULL AND last_fetched_at IS NULL OR last_fetched_at < queued_at`, with a deadline guard.

### Extended `Application` — match status

Add three columns:

```python
match_status: str = "pending_match"   # pending_match | matched | skipped | error
match_attempts: int = 0
match_queued_at: datetime | None
```

The existing `status` column (`pending_review` | `auto_rejected` | `dismissed` | `applied`) keeps its meaning — it's the *user-facing* status. `match_status` is the *worker* status.

Backfill: existing applications with `match_score IS NOT NULL` → `match_status='matched'`; the rest → `match_status='pending_match'`.

### Extended `UserProfile` — sync visibility

```python
last_sync_requested_at: datetime | None
last_sync_completed_at: datetime | None
last_sync_summary: dict | None        # {"new_jobs": 12, "skipped_slugs": ["snowflake"], "stale_slugs_queued": 3}
```

These exist solely to drive the UI's progress chip. They're cheap to maintain and easy to expose via `/api/sync/status`.

### Stale TTL

`Settings.job_stale_after_days`: 14 → **21**. `mark_stale_jobs` continues to mark `is_active=False`; no schema change.

## Components

### 1. `slug_registry_service` (new)

`app/services/slug_registry_service.py`. Owns the `slug_fetches` table.

- `validate_slug(source, slug) -> bool` — calls `GET /v1/boards/{slug}` (cheap ~1-2 KB). 200 → ok + UPSERT a `slug_fetches` row with `last_status='ok'` (no `last_fetched_at` yet — that comes from a real jobs fetch); 404 → reject (no row written). Used by onboarding agent and any future "add company" UI to fail fast before persisting bad slugs.
- `mark_fetched(source, slug, status, error=None)` — updates timestamps, increments/resets counters, flips `is_invalid` after 2 consecutive 404s.
- `enqueue_stale(profile, ttl_hours=6) -> list[str]` — for each slug in `profile.target_company_slugs.greenhouse` that's not `is_invalid`: if `last_fetched_at IS NULL OR last_fetched_at < now() - ttl_hours`, set `queued_at=now()`. Returns enqueued slugs.
- `next_pending(limit, deadline) -> list[(source, slug)]` — claim rows for processing. Skip rows already claimed in the last 5 min (lease pattern, no SELECT FOR UPDATE needed at our scale).

### 2. `job_sync_service.sync_profile` — rewritten

Becomes a fast, **enqueueing-only** operation:

```python
async def sync_profile(profile, session):
    # 1. Seed defaults if empty
    if not slugs(profile):
        profile.target_company_slugs = {"greenhouse": DEFAULT_SLUGS[:5]}
    # 2. Enqueue stale/missing slugs for background fetch
    queued = await slug_registry_service.enqueue_stale(profile, ttl_hours=6)
    # 3. Match against already-cached, slug-scoped, not-yet-scored jobs (capped)
    matched = await match_service.score_cached(profile, session,
                                               cap=settings.matching_jobs_per_batch)
    # 4. Persist sync state
    profile.last_sync_requested_at = now()
    profile.last_sync_summary = {"queued_slugs": queued, "matched_now": len(matched), ...}
    return {"status": "queued", "queued_slugs": queued, "matched_now": len(matched)}
```

`score_cached` is new — variant of `score_and_match` that:
- Filters jobs to `company_name IN (SELECT … FROM profile.target_company_slugs.greenhouse via greenhouse_company_name(slug))`
- Excludes already-scored applications
- Caps to `matching_jobs_per_batch` (20)

### 3. `greenhouse_board.GreenhouseBoardSource` — modified

- Single shared `httpx.AsyncClient` per fetch batch (connection reuse).
- Tighter timeouts: `httpx.Timeout(connect=5, read=15, write=5, pool=5)`.
- New method `validate(slug)` — `GET /v1/boards/{slug}`, returns `True`/`False`.
- `fetch_jobs(slug, since: datetime | None)` — pulls full list (Greenhouse has no `updated_since` filter), client-side filters by `updated_at >= since` if `since` is given.

### 4. `process_sync_queue` cron worker (new)

`POST /internal/cron/process-sync-queue` → `app/scheduler/tasks.py::run_sync_queue()`.

```python
async def run_sync_queue() -> dict:
    deadline = time.monotonic() + 240    # 4-min budget; bail before Cloud Run's 5min wall
    sem = asyncio.Semaphore(8)
    fetched = invalid = transient = 0
    async with httpx.AsyncClient(timeout=...) as client:
        source = GreenhouseBoardSource(client=client)
        async def _one(slug):
            async with sem:
                if time.monotonic() > deadline: return
                # last_fetched_at-aware "since": existing slug → last_fetched_at - 1h overlap;
                # new slug → now() - 14d
                since = ...
                jobs = await source.fetch_jobs(slug, since=since)
                for jd in jobs:
                    job, created = await job_service.upsert_job(jd, "greenhouse_board", session)
                    if created:
                        await match_queue_service.enqueue_for_interested_profiles(job, session)
                await slug_registry_service.mark_fetched("greenhouse_board", slug, "ok")
        # Claim batch and fan out
        slugs = await slug_registry_service.next_pending(limit=64, deadline=deadline)
        await asyncio.gather(*(_one(s) for _, s in slugs), return_exceptions=True)
    return {"fetched": fetched, "invalid": invalid, "transient": transient,
            "remaining": await slug_registry_service.pending_count()}
```

Key trick: when a brand-new job is upserted, we immediately enqueue match work for every active profile whose `target_company_slugs.greenhouse` contains the company. That fan-out is bounded by **#interested-profiles**, not by global user count.

### 5. `match_queue_service` (new)

`app/services/match_queue_service.py`. Thin wrapper over `Application` rows in `match_status='pending_match'`.

- `enqueue_for_interested_profiles(job, session)` — `INSERT INTO applications (job_id, profile_id, match_status='pending_match') ON CONFLICT DO NOTHING` for every active profile whose slug list contains `job.company_name`'s slug. (We need a slug→`Job.company_name` mapping. Today `company_name = slug.replace("-"," ").title()`. We'll keep that and reverse-map: `slug = company_name.lower().replace(" ","-")`. Brittle but consistent with current code; a future Company table cleans this up.)
- `next_batch(limit=30) -> list[Application]` — claim oldest `pending_match` rows.

### 6. `process_match_queue` cron worker (new)

`POST /internal/cron/process-match-queue` → `run_match_queue()`. Drains up to 30 pending applications per tick. Re-uses existing `match_service` machinery (LangGraph agent, semaphore, backoff). On success: `match_status='matched'`. On `score=None` (rate limit / quota): leave `match_status='pending_match'`, increment `match_attempts`, drop after 3 attempts to `match_status='error'`.

### 7. `default_slugs` seed list (new)

`app/data/default_slugs.py` — Python module exporting `DEFAULT_SLUGS: list[str]`. ~15 hand-picked, currently-active Greenhouse-hosted companies. Used by `sync_profile` when `profile.target_company_slugs` is empty.

A pytest in `tests/integration/test_default_slugs.py` runs `validate_slug` against each entry; CI nightly job (extension of cron.yml or a new workflow) re-runs it weekly to catch bit-rot. Failure ≠ test failure on every PR — only on the scheduled run, so it doesn't block unrelated work.

### 8. `/api/sync/status` endpoint (new)

`GET /api/sync/status` → `{ state, slugs_total, slugs_fetched_recently, slugs_pending, matches_pending, matches_done_today, last_completed_at, invalid_slugs }`. Frontend polls every 3s while `state != "idle"`.

`state` derived as:
- `"syncing"` if `pending_count > 0` for the user's slugs in `slug_fetches`
- `"matching"` if any `applications.match_status='pending_match' AND profile_id=...`
- `"idle"` otherwise

### 9. Frontend changes

- Dashboard sync button → `POST /api/jobs/sync`, on `202` show toast: *"Searching now. New matches will appear in a couple minutes."*
- Header chip polled from `/api/sync/status`: e.g. *"Syncing 3 of 12 boards"* → *"Scoring 47 jobs"* → *"Done — 8 new matches"*.
- Auto-refresh the matches list when `state` flips back to `"idle"`.
- "Removed slugs" notice if `invalid_slugs` non-empty: *"We removed `openai`, `slack` — Greenhouse no longer has boards for them."*

## Cron schedule

Update `.github/workflows/cron.yml`:

| Schedule (UTC) | Endpoint | Purpose |
|---|---|---|
| `*/15 * * * *` | `/internal/cron/process-sync-queue` | Drain stale-slug fetch queue (4-min budget per tick) |
| `*/15 * * * *` | `/internal/cron/process-match-queue` | Drain pending matches (4-min budget per tick) |
| `0 */6 * * *` | `/internal/cron/sync` | Bulk-enqueue stale slugs for all active profiles (6h pass) |
| `0 3 * * *` | `/internal/cron/maintenance` | 21d staleness + search auto-pause + 500-app trim |

GHA's minimum cron granularity is 5 min; `*/15` is well within tolerance and matches our 4-min worker budget. If we want sub-15-min responsiveness later, swap to **Cloud Scheduler** (one resource, free tier, same `X-Cron-Secret` contract). Not required for v1.

## Data flow — happy path

1. **User clicks "Sync now"** → `POST /api/jobs/sync`.
2. `sync_profile`:
   a. Seeds 5 defaults if `target_company_slugs` empty.
   b. `enqueue_stale(ttl=6h)` → 3 of 12 slugs older than 6h, `queued_at=now()` set on those rows.
   c. `score_cached(cap=20)` → 8 not-yet-scored jobs from cached slugs match score; 3 above threshold land as `pending_review`.
   d. Returns `202 {status: "queued", queued_slugs: ["airbnb","stripe","notion"], matched_now: 8}`.
3. **Frontend** shows toast + starts polling `/api/sync/status`.
4. Within 15 min: `process-sync-queue` cron fires. Worker:
   a. Claims 3 slugs from `slug_fetches.queued_at IS NOT NULL`.
   b. Fetches concurrently (8-way semaphore).
   c. For each new job: upsert + `match_queue_service.enqueue_for_interested_profiles`.
   d. `slug_registry_service.mark_fetched(..., "ok")` → `last_fetched_at=now()`, `queued_at=NULL`.
5. Within 15 min: `process-match-queue` cron fires. Worker:
   a. Claims up to 30 `pending_match` applications.
   b. Re-uses `match_service` to score them in a single LangGraph batch.
   c. Updates `match_status='matched'` + `match_score` etc.
6. Frontend's next poll sees `matches_pending=0`, `state="idle"`, refreshes matches list.

End-to-end wall clock for a 12-slug profile with 100 new jobs: **typically ~30s**, worst case ~2 cron ticks (~30 min). No HTTP request ever exceeds 5s.

## Data flow — invalid slug

1. User somehow has `openai` in their slug list (legacy, or onboarding inferred it before guardrail).
2. Cron fetch worker calls `GET /v1/boards/openai/jobs` → 404.
3. `mark_fetched("greenhouse_board", "openai", "invalid")` → `consecutive_404_count=1`, `last_status="invalid"`.
4. 6h later, next bulk enqueue fires. Worker fetches again → 404.
5. `mark_fetched(...)` → `consecutive_404_count=2`, **`is_invalid=True`** flipped.
6. From now on, `enqueue_stale` skips this slug. `/api/sync/status` includes `openai` in `invalid_slugs`. Frontend shows the dismissable notice.
7. User removes `openai` from their list (or ignores it; it's idempotent).

## Error handling

| Failure mode | Behavior |
|---|---|
| Greenhouse 404 | `InvalidSlugError`; counts toward `consecutive_404_count`; flips `is_invalid` at 2. |
| Greenhouse 5xx / network | `TransientFetchError`; `consecutive_5xx_count++`, exponential backoff next cycle, never flips invalid. |
| Greenhouse > 15s read timeout | Same as 5xx. |
| LLM rate-limited (`score=None`) | Application stays `pending_match`, `match_attempts++`. Drop to `error` after 3 attempts. Dashboard surfaces a small "Re-scoring soon" indicator. |
| LLM `BudgetExhausted` | Cron worker returns `{status: "budget_exhausted", resumes_at: ...}` (already handled by `_run_cron`); skipped applications stay `pending_match`. |
| Cron worker crashes mid-batch | Claimed rows have `queued_at` set but `last_fetched_at` unchanged. Stale-claim sweep reclaims them after 5 min. |
| Cloud Run cold start during cron | First fetch slow (~30s), but worker has 240s budget — it finishes the batch and returns; subsequent ticks are warm. |
| Two cron ticks overlap (rare) | Lease pattern (`queued_at` newer than 5 min skipped) prevents double-claim. Worst case: one slug fetched twice → idempotent upsert. |

## Testing

Unit tests:
- `slug_registry_service.enqueue_stale` — TTL boundary cases, invalid slug skipped, brand-new slug fetched immediately.
- `slug_registry_service.mark_fetched` — counter increments, is_invalid flip at exactly 2 consecutive 404s, transient doesn't count.
- `greenhouse_board.fetch_jobs(slug, since=...)` — client-side `updated_at` filter correctness.
- `match_queue_service.enqueue_for_interested_profiles` — slug→company_name reverse mapping, idempotent on conflict.

Integration tests (testcontainers Postgres):
- "Sync now" with empty slugs → seeds defaults + enqueues all + matches 0.
- "Sync now" with all slugs fresh (<6h) → enqueues 0, matches against cache.
- "Sync now" with all slugs stale → enqueues all, matches against cache, returns 202 fast (<200ms).
- Cron worker drains queue across 2 ticks when first tick exceeds budget.
- Two profiles with overlapping slug `airbnb` trigger only **one** Greenhouse fetch.
- Invalid-slug pruning happens at exactly 2 consecutive 404s, not 1.

Smoke (live server):
- Click "Sync now" with a real test profile → response time <1s, `/api/sync/status` reaches `idle` within 30s, dashboard updates.

## Migration

1. Alembic migration `add_slug_fetches_and_match_queue`:
   - Create `slug_fetches` table.
   - Add `match_status`, `match_attempts`, `match_queued_at` columns to `applications` (default `'matched'` for existing rows where `match_score IS NOT NULL`, else `'pending_match'`).
   - Add `last_sync_requested_at`, `last_sync_completed_at`, `last_sync_summary` columns to `user_profiles`.
   - Index `(match_status, match_queued_at)` on `applications`.
   - Index `(queued_at)` on `slug_fetches`.
2. Backfill `slug_fetches`: for every distinct slug across all profiles' `target_company_slugs.greenhouse`, insert a row with `last_fetched_at=NULL` (so first cron pass treats them all as new).
3. Update `Settings.job_stale_after_days` default to 21.
4. Deploy. First cron tick after deploy will enqueue and fetch all known slugs once.

## Rollout & observability

- Ship behind no flag. The new `sync_profile` returns 202 instead of synchronous results — that's the only API contract change. Frontend ships in lockstep.
- Add structured logs: `slug_fetch.ok`/`slug_fetch.invalid`/`slug_fetch.transient` (with `slug` and `ms`), `match_queue.drained` (with `count` and `ms`), `sync.queued` (with `queued_slugs`, `matched_now`).
- Cloud Error Reporting already picks up unhandled exceptions via the existing `_add_cloud_run_severity` processor — no change needed.
- Watch in production for 1 week: median sync request latency, p99 fetch latency per slug, queue drain time, % invalid slugs caught.

## YAGNI'd

- Per-user fetch quotas. The 25/day on `MANUAL_SYNC_DAILY_LIMIT` already covers manual abuse; cron is system-driven.
- Cloud Scheduler. GHA cron at `*/15` is sufficient until we have many users. Document the migration trigger ("if median time-to-match > 5 min, switch to Cloud Scheduler").
- A dedicated `Company` table. Today slug→company_name is a deterministic transform; a Company table would be the right shape but isn't required for this change.
- Webhooks from Greenhouse (they don't expose them on the public board API anyway).
- "Discover popular companies" features. Not viable through Greenhouse public API; out of scope.

## Open questions

None blocking — everything above can be implemented as written. Two cosmetic ones worth flagging:

- The slug→company_name reverse map (`company_name.lower().replace(" ","-")`) is brittle if a slug contains numbers or unusual punctuation. The existing forward map (`slug.replace("-"," ").title()`) has the same fragility. We'll add a regression test and accept this as an existing constraint until a `Company` table happens.
- Whether `/api/sync/status` should be polled or pushed via SSE/WS. Polling at 3s is fine for v1; SSE is a future optimization.
