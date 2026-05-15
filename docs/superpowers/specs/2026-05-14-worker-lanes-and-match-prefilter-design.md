# Worker Lanes And Match Prefilter Design

## Context

Production currently uses one physical `work_queue` table and one worker process
that claims any eligible job type from one shared worker pool. A large match
backlog can consume worker capacity even when slow/background work, such as
provider fetching, is ready to run.

Live queue depth on 2026-05-14 showed thousands of pending `match` rows and only
a few `fetch-slug` rows. That is the failure mode this design addresses: LLM
work must be bounded by the API limit, while slow/background work must keep
draining independently.

Matching also performs deterministic remote-policy enforcement after the LLM
score. That still spends LLM capacity on jobs the code can already reject, such
as roles requiring recurring office attendance outside the user's target
locations.

## Goals

- Split one physical worker process into LLM and slow internal worker pools.
- Keep total LLM job concurrency bounded to 6-8 concurrent jobs.
- Allow fetch and maintenance work to drain separately from the LLM backlog.
- Add deterministic pre-checks before LLM match scoring.
- Make deterministic match rejections visible and explicit in the normal
  application review surface.
- Preserve the existing `work_queue` table, dedupe keys, lease ownership,
  retry/backoff behavior, and maintenance cleanup.

## Non-Goals

- Create separate physical queue tables.
- Build a general rules engine or structured job classifier.
- Hide deterministic rejects from the UI.
- Change provider fetch semantics.
- Change cover-letter generation behavior beyond placing it in the LLM lane.

## Worker Lane Model

Keep one physical `work_queue` table and one physical worker process. Inside
that process, start multiple internal worker pools. Each pool has its own
job-type allowlist and concurrency cap.

```text
WORKER_LLM_JOB_TYPES=match,generate-cover-letter
WORKER_LLM_CONCURRENCY=6
WORKER_SLOW_JOB_TYPES=fetch-slug,maintenance
WORKER_SLOW_CONCURRENCY=8
```

Implementation can use asyncio tasks rather than OS threads because the current
worker stack is async. The required invariant is operational, not mechanical:
one process runs multiple independent lane workers, and each lane enforces its
own concurrency limit. If a future blocking operation requires actual threads,
that can be hidden behind the lane worker interface without changing the queue
contract.

An unset lane configuration keeps the current behavior: one unfiltered pool
using `WORKER_CONCURRENCY`. This preserves local development and makes rollback
simple.

`claim_one()` accepts an optional job-type allowlist. When present, the claim
query only considers eligible rows whose `job_type` is in that allowlist. The
same lease timeout, `FOR UPDATE SKIP LOCKED`, ordering, attempts increment, and
not-before semantics continue to apply.

The worker startup log includes:

- `worker_id`
- lane names
- per-lane concurrency
- `visibility_timeout_s`
- per-lane `job_types`, using `all` only for the rollback/default pool

## Production Runtime Shape

Production runs one worker service from the app image. That one process starts
both internal lane pools:

```text
job-search-worker
  command: python -m app.worker
  WORKER_LLM_JOB_TYPES=match,generate-cover-letter
  WORKER_LLM_CONCURRENCY=6
  WORKER_SLOW_JOB_TYPES=fetch-slug,maintenance
  WORKER_SLOW_CONCURRENCY=8
```

The LLM lane owns every job type that can call an LLM:

- `match`
- `generate-cover-letter`

The slow lane owns jobs that should not be blocked by LLM backlog:

- `fetch-slug`
- `maintenance`

The default LLM concurrency should start at `6`. It can be raised to `8` only if
observability shows the provider API limit is not being approached. Cover-letter
generation and match scoring share this same cap so the deployment cannot
accidentally multiply LLM traffic by scaling separate LLM consumers.

The slow-lane concurrency should start at `8`, matching the existing provider
fetch concurrency expectation. It can be tuned independently because these jobs
do not call LLM APIs.

The process-level supervisor owns shutdown. On `SIGTERM` or `SIGINT`, it stops
all lane polling loops, waits for every in-flight lane task to finish, and then
exits. A shutdown must not abandon in-flight jobs differently depending on lane.

## Deterministic Match Prefilter

Before calling `matching_agent.score_one()` in the `match` handler, load the
application's related job and profile and run deterministic pre-checks. The
first pre-check is the existing remote policy:

```text
evaluate_remote_policy(profile, job)
```

If the verdict is a hard mismatch, the handler must not call the LLM. It
persists a visible match result on the application and returns successfully so
the queue row is marked done.

Persisted deterministic rejection shape:

```text
status: auto_rejected, if the application is still pending_review
match_score: below match_score_threshold
match_summary: Deterministic mismatch: recurring office attendance requirement
match_rationale: Requires recurring office attendance outside target locations
match_strengths: []
match_gaps: [Requires recurring office attendance outside target locations]
```

The exact score should be deterministic and below threshold. Use the same
threshold-aware cap currently used by post-LLM remote policy enforcement:
`max(0.0, min(0.29, threshold - 0.01))`.

If the application is already user-owned state such as `dismissed` or `applied`,
the prefilter must not overwrite that status. It may still persist the match
score and rationale fields if the application is being rescored, but user-owned
status remains authoritative.

If the pre-check does not produce a hard mismatch, the handler proceeds to the
current LLM scoring path. The existing post-LLM remote-policy cap should remain
as a defense in depth path for any match scoring entrypoint that does not use
the worker prefilter.

## Observability

Existing queue-depth emission remains useful because all rows stay in
`work_queue`. Add lane context to worker logs so production can distinguish LLM
and slow-lane consumers:

- `worker.started` includes lane configuration
- job claim/start/done/failure logs include `lane`
- job completion/failure logs continue to include `job_type`

Operational queue checks should group by `job_type` and `status`. The important
runtime invariant is that pending slow-lane rows can drain even when `match`
backlog is large.

## Failure Handling

Filtered claiming must not alter finalization semantics:

- `mark_done`, `mark_failed`, and `release_with_backoff` remain lease-owner
  scoped.
- Unknown job types are only claimed by a lane whose allowlist includes them,
  or by the rollback/default unfiltered pool.
- A misconfigured lane with no matching job types simply polls and idles; it
  must not block the other lane.
- If a lane job-type env var contains whitespace, empty entries, or duplicate
  entries, parsing trims and deduplicates the list.

Deterministic prefilter failures are code failures, not domain mismatches. If
loading related data or applying the pre-check raises unexpectedly, normal
worker exception handling applies.

## Testing

Queue service tests:

- `claim_one(..., job_types=["match"])` claims the oldest eligible `match` row
  and ignores older non-match rows.
- `claim_one(..., job_types=["fetch-slug", "maintenance"])` does not claim
  `match` or `generate-cover-letter`.
- `claim_one(..., job_types=None)` preserves current all-job behavior.
- Future `not_before` and stale in-progress reclaim rules still work with a
  job-type filter.

Worker lifecycle tests:

- One worker process starts both LLM and slow lane pools.
- The LLM lane does not process `fetch-slug` or `maintenance`.
- The slow lane does not process `match` or `generate-cover-letter`.
- LLM concurrency is capped independently from slow-lane concurrency.
- Slow-lane rows can complete while the LLM lane is saturated.
- Worker startup accepts comma-separated lane job-type env vars.

Match handler tests:

- A remote-policy hard mismatch persists a visible `auto_rejected` application
  with explicit summary, rationale, and gap.
- The deterministic mismatch path does not call `matching_agent.score_one()`.
- The deterministic mismatch path does not overwrite `dismissed` or `applied`
  status.
- A non-mismatch job still calls `matching_agent.score_one()` and persists the
  normal LLM score.

## Rollout

1. Ship code that supports filtered claiming and internal lane pools while
   leaving lane env vars unset in production.
2. Deploy the code and verify the default unfiltered pool still drains all job
   types.
3. Update the single worker service env to enable the LLM and slow lanes.
4. Verify queue depth grouped by job type:
   slow-lane rows should not wait behind match backlog.
5. Keep the default unfiltered-pool rollback path available for one deploy
   cycle.

Rollback is unsetting the lane env vars so the process returns to the existing
single unfiltered pool using `WORKER_CONCURRENCY`. No database migration
rollback is required.
