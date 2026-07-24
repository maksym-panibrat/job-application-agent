# Batch Match Canary

Use this when changing async matching or the provider batch integration.

## Why this exists

Batch matching saves latency/cost by using provider-side async execution, but it can become unsafe if local applications, provider requests, and model outputs are correlated too cleverly. The canary verifies that a small rollout still imports only the intended results.

## Invariant to protect

Provider batch APIs are an async transport, not permission to put many local applications into one prompt. Correlation should be deterministic and auditable from provider request metadata and local database rows.

If a provider result cannot be correlated confidently, fail or retry that local item instead of importing partial or guessed scores.

## Local checks

Run the unit/integration tests that cover:

- batch request construction;
- provider-result import;
- malformed, duplicate, missing, or unknown provider result keys;
- worker retry/failure behavior for batch-match jobs;
- the canary CLI bounds, payload, idle-profile guard, and candidate budget.

Use current test file names from `tests/`; do not rely on this runbook for an exhaustive test list.

## Production canary

### Safety gate: isolated and disposable only

Use a dedicated synthetic canary account/profile. It must not belong to a real
user, and nobody may use its UI, trigger a sync/rematch, or otherwise mutate it
while the canary is running. Disable its normal search/sync activity before the
run. The required `--ack-isolated-canary-profile` flag is an operator assertion
that these conditions hold; it is not permission to repurpose a low-volume real
profile.

Canary rows are disposable. Capture a run-labeled baseline as **evidence only**
(application IDs and relevant status/score fields, plus the code/image SHA and
timestamp). Do not clear scores or rewrite user decisions to manufacture a
cohort. In particular, never mutate `applied` or `dismissed` rows.

There is currently no audited compare-and-swap restoration tool. Therefore:

- do not run ad-hoc restoration SQL;
- do not replay a baseline over rows that may have changed;
- if unexpected rows change, stop rollout, preserve the evidence, and quarantine
  or dispose of the synthetic data through an approved account/data lifecycle;
- do not use this procedure on data that must be restored.

### Enqueue one bounded run

The script requires an explicit item limit from 1 through the hard maximum of
10. It sends both `max_items` and a lifetime `max_candidates` budget through the
normal typed worker payload. Before each tick, the handler atomically reserves
that row's remaining budget by checkpointing zero under the current queue lease;
a successful tick refunds only the candidates it did not inspect. A crash or
finalization replay can therefore stop a canary early but cannot reuse budget
already exposed to deterministic writes or provider submission. Production jobs
that do not carry `max_candidates` retain their normal behavior.

```bash
uv run python scripts/enqueue_batch_match_canary.py \
  --profile-id <synthetic-profile-uuid> \
  --max-items 3 \
  --ack-isolated-canary-profile
```

`--email <synthetic-account-email>` may be used instead of `--profile-id`.
Before inserting anything, the script refuses to proceed if that profile has a
pending/in-progress `batch-match` queue row or an active provider batch. A
concurrent dedupe conflict is also a refusal: the script uses insert-only
conflict behavior and never resets or upserts an existing queue row. Investigate
and wait for the profile to become idle; do not delete or rewrite queue/batch
rows to force the canary through.

### Evaluate

1. Confirm the command reports the intended profile, `max_items`, and equal
   `max_candidates`. Save the output under the run label.
2. Confirm worker logs show submission, polling, and import. Correlate evidence
   by profile, local batch, provider batch, request key, and application ID.
3. Report `selected`, `deterministic_rejected`, provider `submitted`, `imported`,
   `retryable_failed`, and `terminal_failed`. `selected` includes candidates
   handled by deterministic policy; it must never exceed the acknowledged
   candidate budget for the run.
4. Reconcile every provider request key and application ID. Duplicate, unknown,
   missing, or otherwise uncorrelated results are failures; never accept guessed
   or partial mappings.
5. Compare final synthetic application fields with the evidence baseline and the
   expected threshold outcome. Confirm rows outside the bounded synthetic cohort
   did not change.
6. Do not widen rollout while there are correlation errors, terminal failures,
   unexplained count differences, status disagreement, budget overruns, or
   unexplained score deltas. Keep real-user activity off the profile until the
   investigation is closed; do not attempt ad-hoc row restoration.
