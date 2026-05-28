# LLM Batch Matching Design

## Problem

Matching currently has a latency-oriented production path: each `match` work row
loads one `Application`, applies deterministic rejection policy, calls the
matching LLM, then persists one score. That keeps fresh user-visible work simple
and isolated, but it is expensive for backlog workloads.

The cost pressure is highest when periodic updates fan out across users who
follow hundreds of companies. A single refresh can create many unscored
applications, most of which do not need an immediate answer. Processing that
backlog through one live LLM call per application increases cost, queue time, and
quota exhaustion risk.

## Goals

- Reduce LLM cost and quota pressure for non-urgent match backlog.
- Keep the existing live `match` worker path for fresh, user-visible scoring.
- Batch periodic update, rematch, and backlog repair work through an async
  provider batch API.
- Apply deterministic US/location/remote-office rejection before batch
  submission.
- Preserve existing scoring lifecycle rules: retryable failures remain
  unscored, and user-owned `dismissed` / `applied` statuses are not overwritten.
- Make batch submission and import idempotent enough to survive worker restarts,
  stale leases, and partial provider results.

## Non-Goals

- Do not replace the live `match` job type.
- Do not optimize for sub-minute match latency in the batch lane.
- Do not batch cover-letter generation.
- Do not introduce multi-job prompts in the first version.
- Do not change deterministic location policy.
- Do not change frontend polling semantics in the first version.

## Approach

Add a separate `match-batch` lane for cost-optimized scoring. The live `match`
lane remains the latency lane and continues to score one application at a time.
Periodic refreshes and large rematch/backlog operations enqueue batch work
instead of thousands of live `match` jobs.

The first version submits one LLM request per application inside provider
batch files. That keeps failures isolated, makes import idempotent, and avoids
one malformed multi-job response blocking several applications. Once this is
stable, profile context caching or small multi-job prompts can be evaluated as a
second optimization.

Gemini Batch API is the initial provider target because matching currently uses
Gemini. The provider batch contract is async and non-urgent: submitted jobs can
complete after the originating worker tick, and import happens through a poller.

## Work Selection

Batch work is selected from unscored applications whose answer is not urgent.
Eligible rows:

- `Application.match_score IS NULL`
- `Application.status IN ('pending_review', 'auto_rejected')`
- The linked job is active and still display-eligible.
- The application is not already owned by an active live or batch score attempt.

Live scoring remains appropriate for:

- A small newest/highest-priority slice after a user-initiated sync.
- Manual retry of a specific application.
- Operational smoke tests and development flows.

Batch scoring is appropriate for:

- Periodic refreshes for users following many companies.
- Large profile rematches.
- Backlog repair after prompt/model changes.
- Catch-up after quota exhaustion or worker downtime.

## Data Model

Add durable batch ownership tables rather than overloading `work_queue` payloads.

`llm_match_batches`:

- `id`
- `provider`: `gemini`
- `provider_batch_id`
- `model`
- `prompt_version`
- `status`: `building`, `submitted`, `provider_running`, `importing`, `done`,
  `failed`, `cancelled`
- `reason`: `periodic-refresh`, `rematch`, `backlog-repair`
- `profile_id`
- `submitted_at`
- `completed_at`
- `last_polled_at`
- `last_error`
- `created_at`
- `updated_at`

`llm_match_batch_items`:

- `id`
- `batch_id`
- `application_id`
- `request_key`: stable provider request key, unique within a batch
- `request_hash`: hash of prompt version, model, profile text, and job context
- `status`: `queued`, `submitted`, `succeeded`, `retryable_failed`,
  `terminal_failed`, `imported`
- `score`
- `summary`
- `rationale`
- `strengths`
- `gaps`
- `error`
- `created_at`
- `updated_at`

The item table is the source of truth for dedupe and import. A submitted batch
must not be recreated for the same item set unless the old batch is terminal or
the request hash changes.

## Queue Model

Introduce new work types:

- `match-batch-build`: selects eligible applications, applies deterministic
  rejects, creates batch rows/items, uploads/submits the provider batch, and
  marks the batch `submitted`.
- `match-batch-poll`: polls submitted/running batches and enqueues import work
  when provider output is ready.
- `match-batch-import`: imports provider output and applies scores to
  applications.

The existing `match` type stays unchanged for live scoring.

## Submission Flow

1. Select a bounded group of eligible applications, preferably partitioned by
   `profile_id`, `model`, and `prompt_version`.
2. Load the profile once and render profile text with the existing formatter.
3. For each application, load the job and run existing deterministic policy.
4. Persist deterministic rejects directly without including them in the provider
   batch.
5. For survivors, create one batch item and one provider request per
   application.
6. Submit a provider JSONL/file batch.
7. Store provider identifiers and mark the batch/items submitted.

The batch builder must commit durable local state before and after provider
submission so a restart can identify whether a batch was merely built or was
actually submitted.

## Request Format

Reuse the existing matching prompt semantics and output shape. Each batch item
must include:

- `application_id`
- rendered profile text
- job title, company, location, workplace type
- prompt-truncated job description
- structured output schema equivalent to `ScoreResult`

The provider response must be validated before touching the application row:

- `application_id` must match a known batch item.
- `score` must be `0.0 <= score <= 1.0`, or the item is retryable failed.
- `summary`, `rationale`, `strengths`, and `gaps` must coerce to the existing
  storage shape.

## Import Flow

The importer handles one completed provider batch at a time.

For each successful item:

1. Re-load the current `Application`, `Job`, and `UserProfile`.
2. Skip import if the application already has a score from another path.
3. Re-run deterministic policy before applying the LLM result.
4. Persist score fields.
5. If the score is below threshold, set `status = 'auto_rejected'` only when the
   current status is `pending_review`.
6. Mark the item `imported`.

For failed or malformed items:

- Mark retryable provider/transient failures `retryable_failed`.
- Mark schema-invalid or permanently missing-domain rows `terminal_failed`.
- Leave `Application.match_score` null for retryable failures.

Batch completion is derived from item states. A batch is `done` when every item is
`imported` or terminal. It is `failed` only when the whole provider batch cannot
be recovered.

## Error Handling

- Provider submission failure before provider id exists: leave batch `building`
  or mark `failed`; items remain eligible for another build attempt.
- Provider submission uncertainty after upload/request: mark the batch
  `submitted` only when the provider id is recorded.
- Provider running timeout: continue polling until a configured max age, then
  mark retryable items failed and surface the batch error.
- Missing application/job/profile at import: mark the item terminal failed.
- `score=None` or malformed output: mark the item retryable failed, not
  auto-rejected.
- Duplicate import: skip applications that already have `match_score`.

## Observability

Emit structured logs for:

- batch build counts: selected, deterministic rejected, submitted
- provider batch id and local batch id
- polling state transitions
- import counts: imported, retryable failed, terminal failed, skipped already
  scored
- cost proxy fields: item count, prompt version, model, approximate input bytes

Queue/status endpoints remain unchanged in the first version. A follow-up UI
pass can add a low-priority "batch scoring in progress" indicator if users need
visibility.

## Tests

Unit tests:

- Batch builder excludes deterministic rejects from provider requests.
- Batch builder writes deterministic rejects with below-threshold scores.
- Batch item request hash changes when prompt version/model/context changes.
- Import preserves `dismissed` and `applied`.
- Import only auto-rejects below-threshold scores from `pending_review`.
- Malformed provider output leaves `match_score` null and marks item retryable.
- Duplicate import skips already-scored applications.

Integration tests:

- Build a batch from multiple unscored applications for one profile.
- Poll/import a fake completed provider batch.
- Retry failed items without duplicating imported scores.
- Ensure live `match` jobs still work independently.

Operational verification:

- Run existing match handler and remote policy tests.
- Run a dry-run batch build in staging with provider submission disabled.
- Submit a small real provider batch for a smoke profile before enabling periodic
  batch submission.

## Rollout

1. Add schema, service objects, and fake provider implementation.
2. Add build/import tests using fake provider output.
3. Add real Gemini provider behind a disabled-by-default feature flag.
4. Enable dry-run batch building in production to validate selection counts.
5. Enable real submission for a small cap per tick.
6. Increase caps for periodic refreshes after import and retry metrics look
   stable.
7. Keep live `match` capacity available for urgent jobs throughout rollout.

## Risks

- Duplicate provider submission can waste money. The local state machine and
  request hashes reduce this risk but cannot make provider creation fully
  idempotent.
- Batch latency means new matches can appear after the periodic refresh tick.
  The live lane handles the small urgent slice.
- Large job descriptions can hit provider file or token limits. Submission must
  keep existing prompt truncation and chunk batches by estimated size.
- Provider result files can contain partial failures. Import must be item-based,
  not all-or-nothing.
