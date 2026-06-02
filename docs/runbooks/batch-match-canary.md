# Batch Match Canary

Use this when changing async matching or the provider batch integration.

## Contract

Batch matching uses the provider batch API as an async transport only:

- one local `Application` + `Job` pair per provider request;
- one provider result per provider request;
- import correlates by provider request key, not by model-copied application id;
- duplicate or unknown request keys are unsafe and fail the submitted items without partial import;
- malformed single-item results are retryable for that item only.

This is intentionally less clever than packing many jobs into one prompt. Predictable correlation is more important than throughput here.

## Local checks

```bash
uv run pytest tests/unit/test_batch_match_packing.py \
  tests/integration/test_handler_batch_match.py \
  tests/integration/test_batch_match_service.py -q
```

## Production canary

1. Enable batch matching for a low-volume profile.
2. Confirm `batch-match` rows are claimed by `job-search-worker`.
3. Confirm `llm_match_batches` moves `submitted -> done`.
4. Confirm scored applications keep expected `pending_review` / `auto_rejected` status.
5. Check Axiom for `batch_match` warnings before widening rollout.
