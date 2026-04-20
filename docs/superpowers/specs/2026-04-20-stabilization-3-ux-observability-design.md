# Spec 3: UX + Observability

**Branch:** `stabilization/ux-observability`
**Depends on:** Spec 1 (main), Spec 2 (PR #1, can merge independently)
**Date:** 2026-04-20

## Overview

Spec 3 closes out the stabilization track. Specs 1 and 2 shipped correct server-side behavior and regression tests; Spec 3 fixes the places where failures are invisible. Six surfaces are addressed: frontend streaming error handling, resume extraction error differentiation, Adzuna enrichment observability, Sentry startup confirmation, structured-log entry/exit completeness, and cron workflow response visibility.

Scope is narrow: no new features, no architectural changes, no global error-platform introduction. Every change either surfaces a failure that is currently silent or converts a vague error into an actionable message.

---

## Problem 1 — Frontend streaming error UX

**Files:** `frontend/src/api/client.ts`, `frontend/src/pages/Onboarding.tsx`

### Current gaps

- `api.sendMessage` issues a POST, reads the SSE body, but never checks `res.ok`. A 401, 500, or network error reaches the `ReadableStream` path or is swallowed by the Promise chain without throwing.
- `api.uploadResume` calls `fetch(...).then(r => r.json())` with no `res.ok` check; a 413 or 500 body is parsed as JSON and either blows up silently or returns garbage.
- The SSE parse loop has `catch { /* ignore */ }` — all `JSON.parse` errors, including mid-stream corruption, are dropped.
- `Onboarding.tsx::sendMessage` and `handleUpload` have no `try/catch`. Any rejection from the above leaves `sending=true` or `uploading=true` permanently — the Send button never re-enables.

### Design

**`client.ts` changes:**

1. `sendMessage(message, onChunk, onError?)` — add optional `onError` callback. Check `res.ok` before entering the stream loop; throw `new Error(\`${res.status}: ${body}\`)` on non-2xx. In the SSE loop, replace the silent `catch` with: if the parse error occurs after `[DONE]` has not yet been seen, call `onError(new Error("stream parse error"))`. If `onError` is absent, re-throw (so existing callers that don't pass it surface the error naturally).
2. `uploadResume(file)` — check `res.ok` after `fetch`; throw `new Error(\`${res.status}: ${await res.text()}\`)` on non-2xx.

**`Onboarding.tsx` changes:**

1. Wrap `await api.sendMessage(...)` in `try/catch`. On catch: append an assistant message `{ role: 'assistant', content: '', error: true }` with text "Something went wrong — please try again." Reset `sending=false`.
2. Pass `onError` to `sendMessage`; on parse error mid-stream, append the same error message and reset state.
3. Wrap `await api.uploadResume(...)` in `try/catch`. On catch: set an `uploadError` state string; render it as a red banner below the Resume button. Reset `uploading=false`.
4. The `Message` interface gains `error?: boolean`; error messages render with `bg-red-50 text-red-700` instead of `bg-gray-100`.

No new components. No toast system. Targeted error state per surface.

---

## Problem 2 — Resume extraction error differentiation

**Files:** `app/services/resume_extraction.py`, `app/services/profile_service.py`, `app/api/profile.py`

### Current gaps

`extract_profile_from_resume` catches all exceptions and returns `{}`. `profile_service.save_resume` silently skips extraction if it returns `{}`. The upload endpoint returns `{"profile": ..., "extracted_fields": 0}` whether extraction succeeded, hit a budget limit, or received a completely unparseable file.

### Design

**`resume_extraction.py`:**

```python
class ResumeExtractionError(Exception):
    pass

class LLMUnavailableError(ResumeExtractionError):
    pass

class InvalidResumeError(ResumeExtractionError):
    pass
```

Replace the blanket `try/except Exception: return {}` with:
- `except (ResourceExhausted, BudgetExhausted) → raise LLMUnavailableError(...)`
- `except json.JSONDecodeError → raise InvalidResumeError(...)`
- `except Exception → log + raise ResumeExtractionError(...)`

`extract_profile_from_resume` now raises instead of swallowing. Callers must handle.

**`profile_service.save_resume`:**

Wrap the `extract_profile_from_resume` call in try/except. Return `(profile, extraction_status: Literal["ok", "llm_error", "parse_error", "skipped"])`:
- `LLMUnavailableError` → `"llm_error"`
- `InvalidResumeError` → `"parse_error"`
- Empty resume text (no extraction attempted) → `"skipped"`
- Successful dict → `"ok"`

**`app/api/profile.py` (`POST /api/profile/upload`):**

Add `extraction_status` to the response body. No status-code change (still 200 — the upload succeeded; extraction is best-effort). Frontend uses `extraction_status` to show a targeted message.

**Frontend banner (`Onboarding.tsx` / `handleUpload`):**

After `await api.uploadResume(file)` (which now returns JSON including `extraction_status`):
- `"llm_error"` → "Resume uploaded, but we couldn't extract your profile right now — the AI is temporarily unavailable. Try editing your profile manually."
- `"parse_error"` → "Resume uploaded, but we couldn't read the structure — try a plain-text or clearly formatted PDF."
- `"ok"` or `"skipped"` → no banner (existing behaviour).

---

## Problem 3 — Adzuna scraping observability

**Files:** `app/sources/adzuna_enrichment.py`, `app/services/job_sync_service.py`

### Current gaps

`fetch_full_description` fires and either returns an enriched dict or `None`. Callers never see aggregate counts. `job_sync_service` logs `"job_sync.completed count=N"` but doesn't know how many were enriched.

### Design

**`adzuna_enrichment.py`:**

`fetch_full_description` already has a single-item try/except. Add structured log calls:
- Entry: `adzuna.enrichment.attempt url=<url>`
- Success: `adzuna.enrichment.success url=<url> salary=<bool>`
- Failure: `adzuna.enrichment.failed url=<url> error=<type>`

**`job_sync_service.py`:**

The sync loop already calls `fetch_full_description` per job (or per Adzuna job). Accumulate counters locally: `enriched`, `salary_parsed`, `enrich_failed`. Emit at the end of the Adzuna source loop:

```python
await log.ainfo("adzuna.sync.summary", enriched=enriched, salary_parsed=salary_parsed, failed=enrich_failed)
```

No schema changes. No new table. Purely structured-log additions.

---

## Problem 4 — Sentry startup confirmation

**File:** `app/main.py`

### Current gap

`if settings.sentry_dsn: sentry_sdk.init(...)` — silent when DSN is absent, silent when init throws. There is no way to confirm from logs whether Sentry is active in a given deploy.

### Design

Replace the bare conditional with:

```python
if settings.sentry_dsn:
    try:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            traces_sample_rate=0.1,
            environment=settings.environment,
        )
        dsn_val = settings.sentry_dsn.get_secret_value()
        await log.ainfo("sentry.enabled", dsn_suffix=dsn_val[-4:])
    except Exception as exc:
        await log.awarning("sentry.init_failed", error=str(exc))
else:
    await log.ainfo("sentry.disabled", reason="no_dsn_configured")
```

Never log the full DSN. The `dsn_suffix` (last 4 chars) is enough to confirm which project is configured without leaking credentials.

---

## Problem 5 — Structured-log entry/exit completeness

**Files:** `app/services/match_service.py`, `app/services/generation_service.py` (or `app/agents/generation_agent.py`), `app/services/resume_extraction.py`, `app/services/job_sync_service.py`, `app/api/internal_cron.py`

### Current gap

Error logs are thorough; success paths are sparse. There's no way to read the logs and know how long a match batch took, which generation attempts succeeded, or whether a cron run completed fast or slow.

### Design

Add `time.perf_counter()` entry/exit pattern to each critical boundary:

```python
t0 = time.perf_counter()
# ... operation ...
await log.ainfo("match.score_and_match.completed",
    profile_id=str(profile_id),
    jobs_scored=len(results),
    duration_ms=int((time.perf_counter() - t0) * 1000),
)
```

Surfaces to instrument:
- `match_service.score_and_match` — entry (profile_id, job_count) + exit (jobs_scored, duration_ms)
- `generation_service.generate_materials` or the generation graph entry — entry (application_id) + exit (duration_ms, status)
- `resume_extraction.extract_profile_from_resume` — entry (resume_length) + exit (fields_extracted, duration_ms)
- `job_sync_service.sync_all_sources` — entry + exit (sources, total_new, total_updated, duration_ms)
- Each `/internal/cron/*` handler — entry + exit (handler name, duration_ms)

No changes to existing error logs. No new dependencies (`time` is stdlib).

---

## Problem 6 — Cron workflow response visibility

**File:** `.github/workflows/cron.yml`

### Current gap

Each step uses `curl -sf -X POST ...`. `-f` causes exit code 22 on HTTP ≥400, so the step correctly fails — but the GitHub Actions log shows only `curl: (22) The requested URL returned error: 401`, with no response body, no JSON, no trace ID. Diagnosing a silent 500 requires a separate Cloud Run query.

### Design

Replace `curl -sf` with a pattern that captures response body + status code and echoes both before evaluating:

```yaml
- name: Trigger job sync
  env:
    CLOUD_RUN_URL: ${{ secrets.CLOUD_RUN_URL }}
    CRON_SHARED_SECRET: ${{ secrets.CRON_SHARED_SECRET }}
  run: |
    response=$(curl -s -w "\n%{http_code}" -X POST \
      -H "X-Cron-Secret: $CRON_SHARED_SECRET" \
      "$CLOUD_RUN_URL/internal/cron/sync")
    body=$(echo "$response" | head -n -1)
    code=$(echo "$response" | tail -n 1)
    echo "HTTP $code"
    echo "$body"
    [ "$code" -lt 400 ] || exit 1
```

Same pattern for all three cron jobs. The workflow step still fails on HTTP ≥400, but the body (JSON summary including `synced`, `new_jobs`, `duration_ms`) is visible in the log before the exit.

**Cron response payload completeness:**

`/internal/cron/sync` currently returns `{"status": "ok", ...}`. Verify all three endpoints return structured summaries with at minimum: `status`, `duration_ms`, and a count field. Backfill any that are missing counts.

---

## Testing

### Unit

- **`tests/unit/test_resume_extraction_errors.py`** (new): three cases — `ResourceExhausted` input raises `LLMUnavailableError`, malformed JSON input raises `InvalidResumeError`, valid JSON input returns a dict. All use `patch_llm` / direct monkeypatching of the LLM response.

### Integration

- **`tests/integration/test_cron_endpoints.py`** (new): POST to each cron endpoint via `AsyncClient`, assert response is JSON with at minimum a `status` key and a numeric count key. Does not test side effects — only response shape.

### Frontend

- **`frontend/src/pages/Onboarding.test.tsx`** (new): two tests:
  1. MSW intercepts `/api/chat/messages` → returns `500`; assert error message appears in chat, Send button re-enables.
  2. MSW intercepts `/api/profile/upload` → returns `200 {"extraction_status": "parse_error"}`; assert extraction error banner appears.

---

## Non-goals (explicitly out of scope)

- LangGraph trace IDs surfaced in UI.
- Global toast / error-boundary platform.
- Sentry synthetic event on boot.
- Request-ID propagation across streaming responses (structlog `contextvars` would be the mechanism — deferred).
- Any Phase 3/4 work.

---

## Files touched

### New
- `tests/unit/test_resume_extraction_errors.py`
- `tests/integration/test_cron_endpoints.py`
- `frontend/src/pages/Onboarding.test.tsx`

### Modified
- `app/services/resume_extraction.py` — typed error classes, typed raises
- `app/services/profile_service.py` — return `(profile, extraction_status)`
- `app/api/profile.py` — add `extraction_status` to response
- `app/sources/adzuna_enrichment.py` — per-call structured logs
- `app/services/job_sync_service.py` — enrichment summary log
- `app/services/match_service.py` — entry/exit timing log
- `app/services/generation_service.py` — entry/exit timing log
- `app/api/internal_cron.py` — entry/exit timing log per handler; wire task return values into response JSON
- `app/scheduler/tasks.py` — return structured summary dicts from each task function
- `app/main.py` — Sentry confirmation logging
- `.github/workflows/cron.yml` — curl → response-capturing pattern
- `frontend/src/api/client.ts` — `res.ok` checks, optional `onError`
- `frontend/src/pages/Onboarding.tsx` — try/catch wrappers, error message rendering, extraction banner
