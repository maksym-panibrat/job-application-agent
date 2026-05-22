# Neon Egress Reduction Design

## Context

The Neon free-tier 5GB network-transfer allowance was exhausted within roughly a
week, while the production database is not large at rest. A live read-only
snapshot on 2026-05-21/2026-05-22 UTC showed:

- project synthetic storage: about 147MB
- `jobs`: 8,047 rows, 86MB total
- `jobs.description_raw`: about 34MB total, average about 4.5KB per row
- `jobs.description`: about 31MB total
- `work_queue`: 25,426 live rows, 8MB total
- `applications`: 14,501 rows, 8.5MB total

The exhausted quota is therefore more likely from repeated reads and transfers
than from storage growth. Neon did not have `pg_stat_statements` installed at
inspection time, so the exact per-query transfer ranking was not available.
This design first adds measurement, then reduces the highest-confidence transfer
amplifiers found in code and logs.

## Evidence

From Axiom logs between 2026-05-14 and 2026-05-22 UTC:

- `POST /internal/cron/sync` ran 770 times, about every 15 minutes.
- `POST /internal/cron/generation-reconcile` ran 384 times, about every 30
  minutes.
- `GET /api/sync/status` was requested 27,207 times.
- `GET /api/status` was requested 7,345 times.
- `GET /api/applications?status=pending_review` was requested 1,025 times.
- `auth.token_expired` logged 21,466 times.
- `api.queue_depth` emitted 11,506 samples.

From production database stats:

- `work_queue` has 2,783 completed `fetch-slug` jobs and 22,632 `match` jobs
  since 2026-05-14.
- `jobs` saw 44,288 updates and 13,527 inserts.
- `work_queue` saw 51,429 updates and 25,707 inserts.
- `slug_fetches` saw 370,349 sequential scans over a 116-row table.
- `companies` saw 80,615 sequential scans over a 102-row table.
- All 4 profiles are search-active. Their target company counts are 101, 51,
  29, and 3.

Important code paths:

- `app/services/job_sync_service.py::prune_and_enqueue()` uses a 6-hour slug
  freshness TTL.
- `app/api/internal_cron.py::cron_sync()` delegates to
  `sync_active_profiles()`.
- `app/services/slug_registry_service.py::list_stale_for_profile()` fetches
  companies, then calls `get(provider, slug)` once per provider slug.
- `app/services/job_service.py::upsert_job()` selects full `Job` rows, writes
  full descriptions, commits, and refreshes the ORM object for every posting.
- `app/services/match_service.py::list_applications()` selects `(Application,
  Job)` even though the list API omits job descriptions.
- `app/api/applications.py::get_application()` returns both `description_raw`
  and cleaned `description`.
- `frontend/src/lib/useSyncControl.ts` polls `/api/sync/status` every 3 seconds
  while sync or matching appears live.
- `frontend/src/components/BudgetBanner.tsx` polls `/api/status` every minute.

## Problem

The application treats several operationally frequent paths as if row transfer
were free:

1. Cron sync runs much more often than the slug freshness window needs.
2. Status and feed polling continue at high volume, including after auth expiry.
3. List and worker queries often load full ORM rows that include wide
   description fields.
4. Provider fetch upserts repeatedly read and rewrite existing job rows even
   when the relevant posting content has not changed.
5. The database lacks query-level transfer observability, so future regressions
   would be hard to rank by impact.

The system should preserve full upstream job descriptions in storage. The fix is
not to truncate `description_raw`; it is to stop moving wide description fields
through hot paths that do not need them.

## Goals

- Measure the actual highest-row and highest-frequency queries using
  `pg_stat_statements`.
- Reduce routine Neon network transfer from cron, workers, frontend polling, and
  list endpoints.
- Preserve `jobs.description_raw` as full archival upstream data.
- Keep public response contracts stable unless a response currently exposes data
  the product does not need.
- Keep sync/match behavior functionally equivalent: stale boards still fetch,
  new applications still get matched, and user-owned application states remain
  protected.
- Add tests that catch reintroductions of wide-row reads on hot paths.

## Non-Goals

- Do not delete stored job descriptions.
- Do not change scoring policy, prompt semantics, or LLM model selection.
- Do not redesign the job search product flow.
- Do not replace Neon or add a separate database/cache service in this pass.
- Do not introduce per-user billing or external rate-limit enforcement.
- Do not depend on Neon dashboard transfer totals as the only verification
  signal.

## Approach

Use a phased design:

1. Add measurement and low-risk throttles first.
2. Remove wide-row reads from hot API paths.
3. Make sync and upsert work set-based and change-aware.
4. Add guardrails so polling and stale auth cannot silently recreate the
   transfer spike.

This is preferred over a storage-first cleanup because the database is small at
rest. Truncating descriptions would reduce table size but would violate the
archival data invariant and would not address high-frequency status, cron, and
worker activity.

## Design

### 1. Measurement Window

Install and enable `pg_stat_statements` on production:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

After deploy, reset the stats at the start of a known measurement window:

```sql
SELECT pg_stat_statements_reset();
```

Collect these reports after at least 24 hours:

- highest total rows returned
- highest average rows per call
- most frequent queries
- longest-running queries
- table read/update stats from `pg_stat_user_tables`

Do not treat these reports as exact byte-level egress, because Postgres exposes
rows and execution stats, not true transfer bytes. Rank queries by row count,
frequency, and whether they include wide `TEXT`/`JSONB` columns.

### 2. Cron Cadence And Sync Eligibility

`/internal/cron/sync` should not run every 15 minutes when
`list_stale_for_profile(..., ttl_hours=6)` is the source freshness gate.

Change the scheduler contract to one of:

- run `/internal/cron/sync` every 6 hours, matching the freshness TTL; or
- keep the external 15-minute scheduler, but make the endpoint return early
  unless a global or per-provider-slug eligibility check says there is stale
  work.

The preferred implementation is the 6-hour schedule. It reduces HTTP, auth,
profile, company, and slug-state scans without introducing another internal
state machine.

Generation reconcile can keep its current 30-minute cadence because it is a
small SQL query and is user-visible only when cover-letter generation is stuck.
Maintenance can keep its daily cadence.

### 3. Set-Based Stale Slug Discovery

Replace per-profile, per-company, per-slug lookup loops with one set-based query
that returns distinct stale `(source, slug)` pairs for all active profiles.

The service boundary should separate:

- `list_stale_for_profile(profile_id)` for manual sync progress and tests
- `list_stale_for_active_profiles()` for cron sync

The cron path should not call `prune_and_enqueue()` once per profile if that
causes repeated `Company` and `SlugFetch` reads for shared provider slugs.
Instead, it should:

1. find all active profile target companies
2. expand `companies.provider_slugs`
3. left join `slug_fetches`
4. filter invalid rows and `last_fetched_at < now() - interval '6 hours'`
5. enqueue each distinct provider slug once
6. update affected profiles' `last_sync_*` summaries with bounded payloads

This preserves shared slug freshness across users and prevents four active
profiles from multiplying the same provider-slug scans.

### 4. Narrow Application List Query

The list endpoint must not load job descriptions. Replace the current
`select(Application, Job)` list query with a projection that includes only fields
used by `GET /api/applications`:

- application id
- status
- generation status
- match score
- match summary/rationale/strengths/gaps
- created timestamp
- job id
- title
- company name
- location
- workplace type
- salary
- contract type
- apply URL
- posted timestamp

The API response shape can stay the same with `job.description_raw` and
`job.description` absent or `null` on list responses. Detail responses remain
the place where descriptions are loaded.

Tests should assert that the list service does not select `Job.description_raw`
or `Job.description`. A SQLAlchemy compile-string assertion is acceptable if it
is scoped to the repository's query builder.

### 5. Detail Description Contract

`GET /api/applications/{id}` currently returns both `description_raw` and
cleaned `description`. The frontend display path should use only cleaned
`description`.

Change the detail contract to:

- keep `description`
- omit `description_raw` by default
- optionally expose raw description only behind an explicit admin/debug query
  parameter, if there is a real operator use case

This preserves raw storage while avoiding browser transfer of duplicate
description text. It also prevents future UI code from accidentally rendering
raw HTML.

### 6. Change-Aware Job Upserts

`upsert_job()` should avoid loading and refreshing full `Job` ORM entities for
existing postings.

Use a narrow existence lookup or `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`
that returns only:

- `id`
- whether the row was inserted or updated
- optionally a hash/change flag for description-affecting fields

Add a stored or computed content fingerprint for provider payload fields that
matter:

- title
- company name
- location
- workplace type
- raw description
- salary
- contract type
- apply URL
- posted timestamp

If the incoming fingerprint matches the existing fingerprint, only update
`fetched_at` and leave wide description columns untouched. If it differs, update
the changed content fields and recompute cleaned markdown.

This reduces repeated wide-row movement during scheduled board refreshes while
preserving freshness.

### 7. Worker Scoring Reads

The match worker needs the job description for LLM scoring, but not necessarily
the full `Job` ORM row twice.

Keep deterministic policy behavior intact, but make the read explicit:

- load `Application` narrowly by id
- load only the `Job` columns used by deterministic filters and scoring
- load only the `UserProfile` columns used by profile formatting and policy

This does not eliminate description transfer for real scoring work. It prevents
incidental transfer of unrelated columns and makes future query-stat results
easier to interpret.

### 8. Frontend Polling And Auth Expiry

The client should stop polling on 401 instead of repeatedly hitting authenticated
endpoints with an expired token.

Required behavior:

- if any authenticated API call returns 401, clear the stored token and stop
  sync/status/feed polling until the user signs in again
- `/api/sync/status` polling should use exponential backoff when state remains
  live for more than a short grace period
- idle pages should not poll `/api/sync/status`; the current code already stops
  after idle, but tests should lock this in
- `/api/status` should use TanStack Query or equivalent caching with a longer
  stale time, rather than an unconditional minute interval per mounted app

This directly targets the 27,207 sync-status requests, 7,345 status requests,
and 21,466 expired-token logs seen in the measurement window.

### 9. Queue Depth Observability

`api.queue_depth` emits once per minute per API process. This is acceptable if
there is only one production API process, but it should remain bounded.

Keep the metric, but make it configurable:

- default interval: 60 seconds
- production override allowed by environment
- no more than one emitter per process
- query must remain aggregate-only and never select queue payloads

If the deployment scales API replicas, consider moving queue depth emission to a
single worker or cron-owned process.

### 10. Operator Runbook

Add a short runbook covering:

- how to reset `pg_stat_statements`
- which diagnostic SQL queries to run
- how to compare before/after rows, calls, and hot table stats
- how to identify whether a future spike comes from frontend polling, cron, or
  worker fetch/match activity

This runbook should live near deployment docs, not only in the implementation
plan, because it is an operational task.

## Data Model

Add one optional column to `jobs`:

- `content_hash text null`

Backfill `content_hash` lazily during the next provider refresh or with a small
maintenance migration. The initial migration can add the nullable column only;
the upsert path can populate it for new and changed rows.

No schema change is required for `description_raw` or `description`.

## API Contracts

`GET /api/applications`

- response remains a list of application summaries
- job object remains present
- description fields are not included or are always `null`

`GET /api/applications/{id}`

- includes cleaned `job.description`
- does not include `job.description_raw` by default
- optional debug raw exposure must be authenticated and explicit if added

`GET /api/sync/status`

- response shape remains stable
- server should keep work bounded to counts and small arrays

`GET /api/status`

- response shape remains stable
- client reduces request rate

## Error Handling

- If `pg_stat_statements` is unavailable, startup must not fail. The runbook
  should report that measurement is disabled and instruct the operator to create
  the extension.
- If the sync cron runs before the 6-hour freshness window, it should either not
  run or return a cheap no-op response.
- If content-hash comparison fails for unexpected nulls or old rows, update the
  row normally. Correctness beats skipping a real posting update.
- If a client receives 401, polling stops and the user is treated as signed out.
- If detail callers expect `description_raw`, they must move to the explicit
  debug path or cleaned `description`.

## Testing

Backend tests:

- `list_applications` query does not include `description_raw` or `description`.
- application list response keeps the expected card fields.
- application detail response includes cleaned `description` and omits raw by
  default.
- optional raw-description debug path, if implemented, is explicit and
  authenticated.
- cron sync does not run profile-by-profile stale slug scans when using the
  active-profile helper.
- set-based stale slug discovery deduplicates provider slugs shared by multiple
  profiles.
- unchanged job upsert avoids rewriting description columns and preserves
  `description_raw`.
- changed job upsert updates full `description_raw` and cleaned `description`.

Frontend tests:

- polling stops after a 401.
- `/api/sync/status` is not polled while unauthenticated.
- `/api/sync/status` backs off during long-running live states.
- `/api/status` is cached or interval-bounded.
- application list rendering does not require description fields.
- application detail rendering uses cleaned `description`.

Operational verification:

- enable and reset `pg_stat_statements`
- run for 24 hours
- compare query calls and returned rows against the pre-change evidence
- verify `/internal/cron/sync` request count drops from about 96/day to about
  4/day if the 6-hour schedule is used
- verify `/api/sync/status` and `/api/status` request counts drop after token
  expiry and idle-state fixes
- verify `jobs` updates fall when unchanged provider postings are refetched

## Rollout

1. Deploy measurement and polling/auth fixes first.
2. Change cron cadence and stale-slug discovery.
3. Narrow application list/detail queries.
4. Add content-hash/change-aware job upserts.
5. Reset `pg_stat_statements` and measure for 24 hours.
6. If transfer remains high, use the new query ranking to target the next
   highest contributor.

The changes are mostly backward compatible. The main contract change is removing
default `description_raw` from application detail responses. That is intentional
because raw descriptions are archival storage, not routine UI payload.

## Acceptance Criteria

- `pg_stat_statements` is available or the runbook clearly reports why it is
  unavailable.
- `/internal/cron/sync` no longer runs roughly every 15 minutes in production.
- Application list DB queries do not transfer job description columns.
- Application detail responses do not include raw descriptions by default.
- Unchanged provider postings do not rewrite full job description columns.
- Expired-token polling stops after the first 401.
- A 24-hour post-deploy measurement shows a clear drop in hot endpoint request
  counts and hot query row counts.

