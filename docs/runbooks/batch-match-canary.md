# Batch Match Canary

Use this when validating the real Gemini Batch provider for one profile before turning on automatic batch-match enqueueing.

## Prerequisites

Deploy the app with the provider wired but automatic enqueueing disabled:

```sh
BATCH_MATCH_PROVIDER=gemini
BATCH_MATCH_DRY_RUN=false
BATCH_MATCH_ENABLED=false
GOOGLE_API_KEY=<configured secret>
```

The worker must be running with `batch-match` enabled in its slow job types. Leave `BATCH_MATCH_ENABLED=false` during the canary so normal job ingestion does not enqueue batch work for every affected profile.

## Enqueue One Profile

After the canary user has logged in and the account has unscored applications, enqueue exactly one batch-match job:

```sh
uv run python scripts/enqueue_batch_match_canary.py --email canary@example.com
```

If you already know the profile id:

```sh
uv run python scripts/enqueue_batch_match_canary.py --profile-id <user_profiles.id>
```

The helper writes one `work_queue` row with `job_type=batch-match`, payload `{"profile_id": "..."}`, and dedupe key `batch-match:<profile_id>`. Re-running it resets the pending row for the same profile instead of creating duplicates.

## Evidence To Capture

Record these before enabling automatic enqueueing:

- `work_queue`: the canary row is claimed and finishes or requeues as expected.
- `llm_match_batches`: one batch is created for the profile, has provider `gemini`, has a non-empty `provider_batch_id`, and reaches `done`.
- `llm_match_batch_items`: submitted items transition to `imported` or a clear retryable/terminal failure.
- `applications`: expected rows receive `match_score`, summary, rationale, strengths, and gaps.
- Logs: submit, poll, import, and any provider errors are understandable and include the provider batch id.

Only turn on `BATCH_MATCH_ENABLED=true` after the canary proves submit, poll, output parsing, import, and retry behavior on the deployed worker.
