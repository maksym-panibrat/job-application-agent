# LLM Batch Matching Design

## Problem

Job sync currently creates one `match` work row per unscored application. Each
row loads one `Application`, applies deterministic rejection policy, calls the
matching LLM, then persists one score. That direct path is simple, but it is a
poor fit for the main user-visible workflow:

1. The user clicks sync.
2. Sync enqueues `fetch-slug` work for followed companies.
3. `fetch-slug` upserts jobs and creates applications for interested profiles.
4. Each new application gets its own `match` job.

The expensive part is not latency-sensitive enough to justify one live LLM call
per application. The product can tolerate match scores arriving after the sync
tick, while the business needs lower LLM cost and less quota pressure. The new
primary matching path should therefore use the provider Batch API and should be
shaped for future multi-job prompt optimization.

## Goals

- Make `fetch-slug -> batch-match` the primary production matching workflow.
- Use the provider Batch API to reduce cost and quota pressure for job matching.
- Group multiple jobs for the same profile into one provider request, capped at
  10 applications per request.
- Keep request packing token-aware so large descriptions cannot exceed provider
  limits.
- Keep deterministic US/location/remote-office rejection outside the LLM call.
- Preserve existing scoring lifecycle rules: retryable LLM failures remain
  unscored, and user-owned `dismissed` / `applied` statuses are not overwritten.
- Keep `match` available as a non-primary fallback path for tests, manual repair,
  and emergency rollback.
- Make provider submission, polling, and import restart-safe enough for worker
  crashes, stale leases, and partial provider results.

## Non-Goals

- Do not batch cover-letter generation.
- Do not remove the existing `match` handler in the first implementation.
- Do not require sub-minute scoring latency after sync.
- Do not change deterministic location policy.
- Do not change the user-facing sync action.
- Do not introduce a separate UI surface for batch progress in the first version
  unless status semantics require a minimal compatibility update.

## Architecture

Introduce one new queue job type: `batch-match`.

`batch-match` is a profile-scoped coordinator. Its queue payload is only:

```json
{
  "profile_id": "..."
}
```

The queue row is a signal that the profile may have unscored applications. It is
not a handoff of exact application IDs. `batch-match` owns application selection,
dedupe, deterministic rejects, request grouping, provider submission, polling,
and import.

The existing `match` job type becomes non-primary. It can still score one
application directly when explicitly enqueued, but `fetch-slug` should enqueue
`batch-match` rather than one `match` row per application.

## Fetch-to-Batch Contract

`fetch-slug` remains responsible for fetching one provider slug and upserting
jobs. After each job write, it creates missing `Application` rows for interested
profiles as it does today.

For each affected profile with unscored eligible applications, `fetch-slug`
enqueues:

- `job_type`: `batch-match`
- `payload`: `{"profile_id": "<profile id>"}`
- `dedupe_key`: `batch-match:<profile id>`
- conflict behavior: reset `not_before` only when the existing deduped row is
  still pending

`fetch-slug` must not pass application IDs. Passing IDs would couple fetching to
matching policy, make work from multiple slugs harder to merge, and create stale
payloads when applications are scored before the queued row runs.

The durable batch item table, not `work_queue`, is the source of truth for
application-level ownership.

## Work Selection

When `batch-match` runs for a profile, it selects a bounded set of applications:

- `Application.profile_id = payload.profile_id`
- `Application.match_score IS NULL`
- `Application.status IN ('pending_review', 'auto_rejected')`
- linked `Job.is_active IS TRUE`
- linked job is still display-eligible by the existing feed age policy
- no active batch item already owns the same application for the current model,
  prompt version, and request hash

Selection should order newest/highest-signal jobs first:

1. `Job.posted_at DESC NULLS LAST`
2. `Application.created_at DESC`
3. `Application.id ASC`

For v1, enforce one active local batch per profile. If new applications arrive
while a provider batch is in flight, they remain unscored and are picked up by a
later `batch-match` run after the active batch completes or fails.

## Data Model

Add durable ownership tables rather than overloading `work_queue` payloads.

`llm_match_batches`:

- `id`
- `profile_id`
- `provider`: initially `gemini`
- `provider_batch_id`
- `model`
- `prompt_version`
- `status`: `building`, `submitted`, `importing`, `done`, `failed`
- `submitted_at`
- `completed_at`
- `next_poll_at`
- `last_polled_at`
- `last_error`
- `created_at`
- `updated_at`

`llm_match_batch_items`:

- `id`
- `batch_id`
- `application_id`
- `provider_request_key`: stable key for the provider request containing this
  item
- `request_hash`: hash of prompt version, model, profile text, and job context
- `status`: `submitted`, `retryable_failed`, `terminal_failed`, `imported`
- `score`
- `summary`
- `rationale`
- `strengths`
- `gaps`
- `error`
- `created_at`
- `updated_at`

Recommended constraints and indexes:

- one active batch per `profile_id` where batch status is `building`,
  `submitted`, or `importing`
- one active batch item per `application_id`, `model`, `prompt_version`, and
  `request_hash`
- index active batches by `next_poll_at`
- index batch items by `batch_id` and `status`

Retryable item failure is terminal for the current provider batch attempt, but
not terminal for the application. The application remains unscored, and a future
`batch-match` run may create a new item for it.

## Batch-Match Handler Behavior

`batch-match` is a short-lived state-machine driver. It must not hold a
`work_queue` lease while waiting for the provider.

Handler flow:

1. Load the profile from the payload.
2. If an active submitted batch exists for the profile, poll the provider.
3. If the provider is not ready, set `next_poll_at`, enqueue or reset
   `batch-match:<profile_id>` with `not_before`, and return.
4. If the provider is ready, import results, mark the local batch `done` or
   `failed`, then continue to selection for any newly arrived unscored work.
5. If no active batch exists, select eligible applications.
6. Persist deterministic rejects directly and exclude them from provider
   requests.
7. Pack survivors into same-profile provider requests.
8. Persist batch and item rows before provider submission.
9. Submit the provider batch.
10. Record `provider_batch_id`, mark the batch `submitted`, set `next_poll_at`,
    and enqueue or reset `batch-match:<profile_id>` for polling.

If the handler finds no active batch and no eligible work, it returns without
creating a batch.

## Request Grouping

Each provider request contains up to 10 applications for the same profile.

The provider batch file contains many provider requests. A local batch may map to
one provider batch with many request keys:

```text
llm_match_batches row
  provider_batch_id
  provider request A: applications 1-10
  provider request B: applications 11-20
  provider request C: applications 21-23
```

Packing rules:

- group only within one `profile_id`, model, and prompt version
- hard cap: 10 applications per provider request
- soft cap: estimated request tokens/bytes must fit under the configured batch
  request budget
- preserve selection order while packing
- if adding an application would exceed either cap, flush the current group and
  start a new one
- if one application is still too large after normal job-description truncation,
  apply a stricter batch-specific truncation for that application

The estimate should include:

- system prompt
- structured output instruction/schema
- rendered profile text
- job title, company, location, workplace type
- truncated job description for every application in the group
- reserved output budget for up to 10 `ScoreResult` objects

## Request Format

Reuse existing matching semantics, but change the batch prompt shape from one job
to a list of jobs.

Each provider request includes:

- rendered profile text once
- up to 10 job contexts
- each job context includes `application_id`, title, company, location,
  workplace type, and prompt-truncated description
- instruction to return one result per application

The response schema is equivalent to:

```json
{
  "results": [
    {
      "application_id": "...",
      "score": 0.82,
      "summary": "...",
      "rationale": "...",
      "strengths": ["..."],
      "gaps": ["..."]
    }
  ]
}
```

Validation before touching application rows:

- every returned `application_id` must belong to an item in the provider request
- every submitted item must have exactly one returned result or a recorded item
  failure
- `score` must be `0.0 <= score <= 1.0`
- `summary`, `rationale`, `strengths`, and `gaps` must coerce to the existing
  storage shape
- malformed output for one item must not block valid sibling items in the same
  provider request

## Import Flow

Import is item-based even though provider requests can contain up to 10
applications.

For each valid result:

1. Re-load the current `Application`, `Job`, and `UserProfile`.
2. Skip import and mark the item imported if the application already has
   `match_score`.
3. Re-run deterministic policy before applying the LLM result.
4. If deterministic policy now rejects the job, persist the deterministic score
   and deterministic summary/gaps instead of the LLM result.
5. Otherwise persist score, summary, rationale, strengths, and gaps.
6. If the final score is below threshold, set `status = 'auto_rejected'` only
   when current status is `pending_review`.
7. Mark the item imported.

For failed or malformed results:

- provider/transient failures become `retryable_failed`
- schema-invalid responses become `retryable_failed`
- missing application/job/profile rows become `terminal_failed`
- `score = null` or out-of-range scores become `retryable_failed`
- retryable failures leave `Application.match_score` null

A batch is `done` when every item is `imported`, `retryable_failed`, or
`terminal_failed`. It is `failed` when the provider batch cannot be recovered or
submission cannot be reconciled.

## Submission and Idempotency

Provider submission has an unavoidable crash window: the provider can accept a
batch before the local worker records `provider_batch_id`.

To reduce duplicate paid submissions:

- persist the local batch and item rows before provider submission
- include a stable provider request key for every request
- if the provider supports a caller-supplied idempotency key or display name,
  use the local batch id
- record `provider_batch_id` immediately after submission returns
- if submission outcome is unknown, mark the local batch `failed` with
  `last_error` and do not automatically rebuild it in the same handler attempt
- allow a later operator or maintenance path to retry failed unknown-submission
  batches after checking provider state

Normal retryable item failures do not require operator action. They become
eligible for a future `batch-match` run because the application remains unscored
and the old batch is terminal.

## Queue and Worker Configuration

Add `batch-match` to the slow worker lane by default. It performs database work,
provider submission, provider polling, and import, but it should not consume the
same lane capacity as live cover-letter generation.

Recommended defaults:

- LLM lane: `match,generate-cover-letter`
- slow lane: `fetch-slug,maintenance,batch-match`

Queue priority should keep `fetch-slug` ahead of `batch-match` so fetching can
finish creating applications before scoring begins. `batch-match` should run
after `maintenance` or at the same priority as other background work.

`batch-match` re-enqueues itself for polling with `not_before = next_poll_at`.
The queue row remains short-lived and should finish before the worker visibility
timeout.

## Sync Status Semantics

The first implementation should preserve the existing user-facing sync action.
However, once `fetch-slug` stops enqueueing primary `match` rows, sync status
must not report fully idle while a profile has active batch matching work.

Minimal compatibility update:

- count active `batch-match` queue rows for the profile
- count active `llm_match_batches` rows for the profile
- report the existing `"matching"` state when either count is non-zero
- keep the response shape stable unless the frontend needs an additional count

This preserves current polling semantics while making the new primary path
visible enough to avoid false idle states.

## Observability

Emit structured logs for:

- `fetch-slug` affected profile count and `batch-match` enqueue count
- `batch-match` selected, deterministic rejected, submitted, and skipped counts
- local batch id, provider batch id, model, prompt version
- provider request count and item count
- request packing stats: max apps per request, estimated input size, truncation
  count
- polling transitions and provider status
- import counts: imported, retryable failed, terminal failed, skipped already
  scored
- duplicate/unknown submission failures

## Tests

Unit tests:

- `fetch-slug` enqueues one deduped `batch-match` row per affected profile.
- `batch-match` selection ignores payload application IDs because none exist.
- One active batch per profile is enforced.
- Deterministic rejects are persisted and excluded from provider requests.
- Packing caps provider requests at 10 applications.
- Packing starts a new provider request when the estimated token budget would be
  exceeded.
- Request hash changes when prompt version, model, profile text, or job context
  changes.
- Import preserves `dismissed` and `applied`.
- Import only auto-rejects below-threshold scores from `pending_review`.
- Malformed output for one application does not block valid sibling results.
- Retryable failed items leave `match_score` null and become eligible after the
  batch is terminal.
- Duplicate import skips already-scored applications.

Integration tests:

- Sync/fetch creates applications and enqueues `batch-match`, not primary
  per-application `match` rows.
- A fake provider batch scores multiple applications for one profile.
- Polling re-enqueues `batch-match` with `not_before` while provider work is not
  ready.
- Import handles partial success from a provider request containing multiple
  applications.
- New applications created while a profile batch is active are picked up by a
  later `batch-match` run.
- Existing direct `match` jobs still work as fallback.
- Sync status reports `"matching"` while active local/provider batch work exists.

Operational verification:

- Run existing match handler and remote policy tests.
- Run a dry-run `batch-match` build in staging with provider submission disabled.
- Submit a small real provider batch for a smoke profile.
- Compare costs and scores against a small direct-match sample before making
  `fetch-slug -> batch-match` the production default.

## Rollout

1. Add schema, service objects, fake provider implementation, and handler tests.
2. Add `batch-match` handler behind a disabled-by-default feature flag.
3. Add token-aware request packing with a 10-application hard cap.
4. Teach `fetch-slug` to enqueue `batch-match` when the feature flag is enabled.
5. Add sync status compatibility for active batch work.
6. Enable dry-run batch building in production to validate selection and packing
   counts without provider submission.
7. Enable real provider submission for a small per-profile/per-tick cap.
8. Move ordinary sync traffic to `fetch-slug -> batch-match`.
9. Keep direct `match` available for manual fallback until batch metrics are
   stable.

## Risks

- Duplicate provider submission can waste money if the worker crashes after
  provider acceptance but before recording `provider_batch_id`. Stable provider
  keys and conservative unknown-submission handling reduce this risk.
- Multi-job requests make a malformed provider response affect up to 10
  applications. Item-based import and per-result validation limit the blast
  radius.
- Large job descriptions can exceed request limits. Token-aware packing and
  stricter batch truncation are required before submission.
- Batch latency means scores may appear later than they do today. Sync status
  should continue showing `"matching"` while active batch work exists.
- One active batch per profile can defer newly arrived applications until the
  current batch completes. This is intentional for v1 and can be relaxed later if
  throughput requires it.
