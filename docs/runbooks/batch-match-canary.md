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
- worker retry/failure behavior for batch-match jobs.

Use current test file names from `tests/`; do not rely on this runbook for an exhaustive test list.

## Production canary

1. Enable batch matching for a low-volume profile or narrow cohort.
2. Confirm worker logs show batch jobs being submitted, polled, and imported.
3. Confirm scored applications move only into expected review/rejection states for that code version.
4. Check logs for batch-match warnings or failed imports before widening rollout.
5. Disable or narrow the rollout if correlation errors appear.
