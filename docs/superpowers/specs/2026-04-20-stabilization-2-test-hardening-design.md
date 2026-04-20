# Stabilization Spec 2 — Test Hardening Design

## Context and goal

Spec 1 (Critical Fixes) shipped on 2026-04-20. It fixed the event loop, wired `safe_ainvoke`, flipped prod auth, and hardened config validation. No regression tests were added for those fixes — they are currently protected only by the fact that they were just written.

Spec 2 has three goals:

1. **Lock Spec 1 fixes into regressions.** If anyone breaks `safe_ainvoke` wiring, async-native matching, or prod secret validation, a test fails before merge.
2. **Migrate MagicMock-based LLM tests to `FakeListChatModel`.** The three files still using bespoke `MagicMock` fakes (`test_onboarding_agent.py`, `test_match_scoring.py`, `test_match_service.py`) don't exercise prompt/tool-call structure. The production `ToolCapableFakeLLM` shim already exists — tests just don't use it.
3. **Backfill the worst coverage gaps.** `generation_agent.py` has zero tests. `rate_limit_service.py`, the `AUTH_ENABLED=true` JWT path, and application lifecycle state transitions are exercised only indirectly (or not at all).

## Scope decisions

- **All 4 new backend test files** + 4-5 frontend tests + CI coverage gates + optional pre-commit hook.
- **Coverage threshold**: measure baseline after adding `pytest-cov`, set `--cov-fail-under=(baseline − 2)`. Same for frontend with v8 provider.
- **Frontend API mocking**: MSW (Mock Service Worker) v2. Network-layer intercept, cleaner assertions on request shape, avoids coupling tests to `api/client.ts` internals.
- **Keep `ToolCapableFakeLLM`** from `app/agents/test_llm.py`. It already extends `FakeListChatModel` and handles `bind_tools`. The gap is a shared per-test injection fixture — not the class itself. The static `_DEFAULT_RESPONSES` path stays for e2e/Playwright.

## Architecture

### Backend test fixture

Create `tests/conftest.py` (new root-level, applies to all test scopes):

```python
import pytest
from unittest.mock import patch
from app.agents.test_llm import ToolCapableFakeLLM

@pytest.fixture
def make_llm(responses: list[str]):
    """Return a ToolCapableFakeLLM cycling through `responses`."""
    return ToolCapableFakeLLM(responses=responses)

def patch_llm(module_path: str, responses: list[str]):
    """
    Context-manager helper: monkeypatches get_llm() at `module_path`
    to return ToolCapableFakeLLM(responses=responses).

    Usage:
        with patch_llm("app.agents.onboarding", ["response 1", ...]):
            result = await graph.ainvoke(...)
    """
    fake = ToolCapableFakeLLM(responses=responses)
    return patch(f"{module_path}.get_llm", return_value=fake)
```

`patch_llm` is a function that returns a `unittest.mock.patch` context manager — callers use it with `with patch_llm(...)`. This keeps it usable in both `@pytest.fixture` injection style and inline `with` blocks inside tests.

### LLM response format

`FakeListChatModel` cycles through `responses: list[str]`, returning each as an `AIMessage`. For tool-call tests, the response string must be a JSON dict matching the tool schema. The `ToolCapableFakeLLM` already handles `bind_tools` by returning `self` — the tool-call structure in the response string is what gets parsed.

For matching-agent tests, the tool-call response format is:
```json
{"score": 0.85, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}
```

### Frontend MSW setup

MSW v2 in Node mode for Vitest:

**`frontend/src/test/handlers.ts`** — default request handlers:
```typescript
import { http, HttpResponse } from 'msw'

export const handlers = [
  http.get('/api/me', () => HttpResponse.json({ id: '1', email: 'test@test.com' })),
  http.get('/api/status', () => HttpResponse.json({ budget_exhausted: false, resumes_at: null })),
  http.get('/api/applications', () => HttpResponse.json([])),
  http.get('/api/profile', () => HttpResponse.json(null)),
]
```

**`frontend/src/test/server.ts`** — Node server lifecycle:
```typescript
import { setupServer } from 'msw/node'
import { handlers } from './handlers'
export const server = setupServer(...handlers)
```

**`frontend/src/test/setup.ts`** — wire server start/reset/close:
```typescript
import '@testing-library/jest-dom'
import { server } from './server'
beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

Individual tests override handlers using `server.use(http.get(...))` for case-specific responses.

### Coverage tooling

**Backend**: `pytest-cov` in pyproject.toml dev deps. Run:
```bash
uv run pytest tests/unit tests/integration --cov=app --cov-report=term-missing
```
Capture the "TOTAL" line percentage. Set in `ci.yml`:
```yaml
uv run pytest tests/unit/ tests/integration/ --cov=app --cov-fail-under=<baseline-2>
```

**Frontend**: Add to `frontend/vite.config.ts` `test` block:
```typescript
coverage: {
  provider: 'v8',
  reporter: ['text', 'lcov'],
  thresholds: { lines: <baseline-2> }
}
```
`@vitest/coverage-v8` package required.

## Component designs

### 1. `tests/conftest.py` (new)

Exports `patch_llm(module_path, responses)`. No fixtures — keeps it usable in non-fixture contexts. Tests import `patch_llm` directly.

### 2. Migrate `tests/integration/test_onboarding_agent.py`

Drop `_make_fake_llm`. Replace all `patch("app.agents.onboarding.get_llm", ...)` with `with patch_llm("app.agents.onboarding", [AIMessage(content="...")])`. The existing three tests remain; only the mock mechanism changes.

### 3. Migrate `tests/integration/test_match_scoring.py`

Drop `_make_llm_mock`. Replace with `patch_llm("app.agents.matching_agent", responses=["<json>", ...])` where each response is a JSON string matching the `record_score` tool schema. Existing four tests remain.

### 4. Migrate `tests/unit/test_match_service.py`

The 46 `Mock|patch` calls span four areas: `profile_service` calls (`get_skills`, `get_work_experiences`), DB `get_or_create_application`, `build_graph`, and `get_settings`. The unit tests validate threshold logic and log output — they don't need the full DB or LLM. Simplification approach:

- Replace `_make_profile()` MagicMock with a real `UserProfile` object constructed directly.
- Replace `_make_job()` / `_make_application()` MagicMocks with real model instances.
- Keep the `patch` for `build_graph` (the matching graph is an integration concern, not a unit concern here).
- Replace DB session mock with `AsyncMock` at the `get_or_create_application` boundary only.

This reduces mock depth while keeping unit-test speed.

### 5. `tests/unit/test_generation_agent.py` (new)

Tests the three async generator nodes (`generate_resume_node`, `generate_cover_letter_node`, `answer_custom_questions_node`) and the `interrupt` + resume flow.

Key test cases:
- `test_resume_node_returns_doc`: calls `generate_resume_node` directly with scripted LLM response, asserts `GeneratedDoc` shape in returned state.
- `test_cover_letter_node_returns_doc`: same pattern for cover letter.
- `test_graph_interrupts_at_review`: invokes the full graph, asserts it reaches `__interrupted__` status at the review node.
- `test_graph_resumes_after_approval`: provides `user_decision = {"approved": True}` on resume, asserts graph reaches `END` with all documents populated.

Since nodes are `async def` functions now (after Spec 1), they can be invoked directly without building the full graph for simple input/output tests.

### 6. `tests/integration/test_rate_limit_service.py` (new)

Uses the integration `db_session` fixture (real Postgres via testcontainers).

Key test cases:
- `test_check_rate_limit_passes_under_limit`: single call, no exception.
- `test_check_rate_limit_raises_at_limit`: calls exactly `limit+1` times, asserts 429 on the last.
- `test_sliding_window_resets`: mock the clock to force a new window start, assert counter resets.
- `test_check_daily_quota_passes_under_limit`: basic pass case.
- `test_check_daily_quota_raises_at_limit`: same exhaustion test for daily quota.

Note: `rate_limit_service.py` does not check `ENVIRONMENT` itself — the callers gate it. These tests exercise the service function directly; caller-level gating is covered by the existing router tests.

### 7. `tests/integration/test_auth_oauth.py` (new)

Uses `httpx.AsyncClient` with the FastAPI ASGI app, `AUTH_ENABLED=true` in env.

Key test cases:
- `test_protected_route_without_token_returns_401`: GET `/api/applications` with no `Authorization` header returns 401.
- `test_protected_route_with_valid_jwt_returns_200`: mint a valid JWT using `fastapi_users`' `JWTStrategy`, pass as Bearer, assert 200.
- `test_protected_route_with_expired_jwt_returns_401`: mint a JWT with `lifetime_seconds=0` (or backdate), assert 401.
- `test_protected_route_with_invalid_jwt_returns_401`: pass a random string as Bearer, assert 401.

OAuth callback mocking: the Google OAuth exchange endpoint is not tested here (requires a live Google redirect). The relevant seam is the JWT mint/decode, which is fully within the app.

### 8. `tests/integration/test_application_service_lifecycle.py` (new)

Tests state transitions in `generate_materials()`.

Key test cases:
- `test_generate_materials_sets_generating_then_ready`: seeds an `Application` in `pending_review`, calls `generate_materials()` with scripted LLM, asserts `generation_status == "ready_for_review"` and documents persisted.
- `test_generate_materials_not_found_no_error`: passes a nonexistent UUID, asserts function returns without error.
- `test_generate_materials_max_attempts_skipped`: seeds an application with `generation_attempts=3`, asserts function returns without calling LLM.
- `test_save_documents_upserts_on_retry`: calls `save_documents()` twice with the same `doc_type`, asserts only one document row exists (upsert).

### 9. MSW setup + frontend tooling (tasks 2, 3)

As described in Architecture section above. Install via:
```bash
npm install --save-dev msw @vitest/coverage-v8
npx msw init public/ --save
```

### 10. `frontend/src/components/BudgetBanner.test.tsx` (new)

```typescript
it('renders nothing when budget is not exhausted')
it('renders banner when budget_exhausted=true')
it('renders resumes_at date in the banner')
it('renders "next month" when resumes_at is null')
```

Uses `server.use(http.get('/api/status', ...))` to override the default handler per test.

### 11. `frontend/src/components/MatchCard.test.tsx` (new, replaces `src/test/MatchCard.test.tsx`)

The existing file tests the `Matches` page (mislabeled). Delete it; create a proper `MatchCard.test.tsx` in `src/components/`.

```typescript
it('renders job title, company, match score badge')
it('renders strengths and gaps')
it('thumbs-up click calls setInterest(interested)')
it('second thumbs-up click toggles back to null')
it('review link navigates to /matches/:id')
it('dismiss button calls reviewApplication(dismissed)')
```

Uses `@testing-library/user-event` for clicks and `@tanstack/react-query` `QueryClientProvider` wrapper.

### 12. `frontend/src/context/AuthContext.test.tsx` (new)

```typescript
it('starts loading=true, becomes false after getMe resolves')
it('sets user when getMe succeeds')
it('stays user=null when getMe fails with no stored token')
it('loads token from sessionStorage and calls getMe')
it('clears state and redirects on signOut')
```

Uses MSW to control `/api/me` responses. Wraps under `MemoryRouter` + `AuthProvider`.

### 13. `frontend/src/api/client.test.ts` (new)

Unit tests for the `ApiClient` class methods. Bypasses MSW — uses `vi.spyOn(global, 'fetch')` directly since these test the client's own error-handling logic, not the HTTP contract.

```typescript
it('getMe returns parsed user on 200')
it('getApplications throws on non-2xx')
it('setInterest sends PATCH with correct body')
it('uploadResume throws on non-2xx')
it('sendMessage SSE: yields parsed delta strings')
```

Note: `sendMessage` tests the streaming parser. Spec 3 will add the error-propagation behavior; this spec adds the success-path contract.

### 14. `.pre-commit-config.yaml` (optional)

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]
      - id: unit-tests
        name: unit tests
        entry: uv run pytest tests/unit -x -q
        language: system
        pass_filenames: false
        stages: [pre-push]
```

Runs lint on commit, unit tests on push. Install: `pre-commit install && pre-commit install --hook-type pre-push`.

## File map

### New files
- `tests/conftest.py`
- `tests/unit/test_generation_agent.py`
- `tests/integration/test_rate_limit_service.py`
- `tests/integration/test_auth_oauth.py`
- `tests/integration/test_application_service_lifecycle.py`
- `frontend/src/test/handlers.ts`
- `frontend/src/test/server.ts`
- `frontend/src/components/BudgetBanner.test.tsx`
- `frontend/src/components/MatchCard.test.tsx`
- `frontend/src/context/AuthContext.test.tsx`
- `frontend/src/api/client.test.ts`
- `.pre-commit-config.yaml`

### Modified files
- `tests/unit/test_match_service.py` — reduce mock depth
- `tests/integration/test_onboarding_agent.py` — swap LLM mock
- `tests/integration/test_match_scoring.py` — swap LLM mock
- `frontend/src/test/setup.ts` — add MSW server lifecycle
- `frontend/vite.config.ts` — add coverage config
- `frontend/package.json` — add msw, @vitest/coverage-v8
- `pyproject.toml` — add pytest-cov
- `.github/workflows/ci.yml` — add `--cov` flags

### Deleted files
- `frontend/src/test/MatchCard.test.tsx` (mislabeled, replaced by component-level test)

## Verification

- `uv run pytest tests/unit tests/integration tests/e2e -v` — 103+ tests pass.
- `uv run pytest tests/unit tests/integration --cov=app` — coverage at or above baseline.
- `rg "MagicMock|AsyncMock" tests/integration/test_onboarding_agent.py tests/integration/test_match_scoring.py` — zero hits.
- `cd frontend && npm run test` — all vitest tests pass.
- `cd frontend && npm run test -- --coverage` — frontend threshold met.
- CI run on the branch — both Python and frontend coverage gates green.
- `pre-commit run --all-files` succeeds.
