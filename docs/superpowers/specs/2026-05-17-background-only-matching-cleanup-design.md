# Background-Only Matching Cleanup Design

## Problem

Manual sync previously had a synchronous "instant feedback" path that could score cached jobs before the worker saw them. Cron sync also carried its own duplicate stale-slug enqueue loop instead of using the same service as manual sync. Together, that created two matching entrypoints and two sync implementations:

- Worker path: claims `work_queue` match rows and applies deterministic pre-LLM filters before calling the LLM.
- Synchronous path: `sync_profile()` could call `score_cached()`, which used the batch LangGraph path and only applied location policy after LLM scoring.
- Cron path: `/internal/cron/sync` duplicated stale-slug enqueue and profile summary logic instead of delegating to `job_sync_service`.

The worker path is now the safe operational path. Leaving the old synchronous scoring API in place makes it easy for future code to bypass the pre-LLM filters and spend LLM quota on jobs that should be deterministic rejects.

## Goals

- Make manual sync enqueue-only.
- Make cron sync use the same enqueue-only service contract as manual sync.
- Remove the obsolete cached synchronous scoring helper.
- Keep the public response shape stable for the frontend: `status`, `queued_slugs`, `matched_now`, `pruned_slugs`.
- Keep the internal cron response structured: `enqueued`, `pruned`, `active_profiles`.
- Preserve user-owned application states: `dismissed` and `applied` remain protected by the worker handler.
- Keep the single-job worker scoring path as the production scoring entrypoint.

## Non-Goals

- Do not change queue claiming, worker lanes, or retry policy.
- Do not change the deterministic policy rules themselves.
- Do not remove defensive post-score policy capping from `score_one()` in this cleanup; it remains useful if a test or maintenance script calls `score_one()` directly.
- Do not change frontend polling semantics.
- Do not change generation reconcile or maintenance cron behavior beyond import cleanup.

## Design

`app/services/job_sync_service.py` becomes the single sync orchestration module. `sync_profile()` delegates only to `prune_and_enqueue()` and returns the same summary shape with `matched_now=0`. A new active-profile sweep helper in the same module owns the cron loop: select active profiles, call `prune_and_enqueue()` for each one, aggregate queued slug and pruned counts, and return the structured cron summary.

`app/api/internal_cron.py` should not import `slug_registry_service`, `FetchSlugPayload`, or `enqueue` for sync. Its `/sync` endpoint gets a session, delegates to the active-profile sync helper, logs the aggregated summary, and returns it. `app/scheduler/tasks.py` should use the same helper so scheduled/background cron execution and HTTP cron execution cannot drift.

`app/services/match_service.py` keeps profile formatting, application listing, and rematch enqueueing. The obsolete synchronous cached/batch scoring helpers are removed because production scoring is owned by the worker queue.

The worker handler in `app/worker/handlers/match.py` remains the single production scoring entrypoint. It loads the `Application`, `Job`, and `UserProfile`; applies `evaluate_us_location_policy()` and `evaluate_remote_policy()` before importing/calling `matching_agent.score_one()`; and persists deterministic auto-rejects without spending LLM quota.

## Data Flow

1. User calls `POST /api/jobs/sync`.
2. API calls `job_sync_service.sync_profile()`.
3. Sync prunes invalid slugs, enqueues stale `fetch-slug` work, updates `last_sync_*`, and returns `202`.
4. Worker drains `fetch-slug`, creates `Application` rows, and enqueues `match` work.
5. Worker drains `match` rows through pre-LLM deterministic filters, then calls the LLM only for jobs that pass those filters.

Cron flow:

1. Supercronic calls `POST /internal/cron/sync`.
2. Cron endpoint verifies `X-Cron-Secret`.
3. Cron endpoint calls the shared active-profile sync helper.
4. The shared helper applies the same prune/enqueue behavior as manual sync for every active profile.
5. Worker drains all queued fetch and match rows.

## Error Handling

- Sync failures remain request-scoped database/API failures.
- Cron sync failures remain cron-scoped database/API failures handled by the endpoint/worker logging paths.
- Match failures remain worker-scoped transient or terminal failures.
- If deterministic filters reject a job, the worker writes a below-threshold score, summary, rationale, and gaps, then marks `pending_review` rows `auto_rejected`.
- Existing `dismissed` and `applied` statuses are not overwritten by deterministic rejects.

## Tests

- Update `test_sync_profile_returns_202_shape_and_enqueues_stale_slugs` to assert `sync_profile()` does not import or call the matching service.
- Add cron coverage proving `/internal/cron/sync` delegates to the shared active-profile sync helper.
- Add service coverage for the active-profile helper's aggregation contract.
- Remove tests that directly exercise `score_cached()`.
- Keep worker prefilter tests that assert `matching_agent.score_one()` is not called for deterministic rejects.
- Run targeted job sync, cron, handler, and remote policy tests.

## Rollout

This is code-only and backward compatible at the API response level. After deploy, failed match queue rows can be safely requeued because the worker path now owns matching and applies pre-LLM filters consistently.
