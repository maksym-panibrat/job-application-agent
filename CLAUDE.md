# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development setup

```bash
docker compose up -d db
cp .env.example .env   # fill in GOOGLE_API_KEY at minimum
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173 (proxies /api → localhost:8000)
```

`AUTH_ENABLED=false` by default — all requests treated as a single dev user, no login needed.

## Commands

```bash
# Lint
uv run ruff check app/ tests/

# Tests
uv run pytest tests/unit/ -v                                    # fast, no DB
uv run pytest tests/integration/ -v                            # requires Docker (testcontainers)
uv run pytest tests/e2e/ -v                                    # full stack
uv run pytest tests/smoke/ -v --has-seed-api                   # against live server (localhost:8000)
uv run pytest tests/smoke/ -v --base-url http://host:8000      # custom server URL

# Single test
uv run pytest tests/path/to/test.py::test_name -v

# Migrations
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run alembic downgrade -1

# Frontend
cd frontend && npm run dev          # dev server
cd frontend && npm run build        # build to app/static/
cd frontend && npm test             # component tests (vitest)
cd frontend && npm run test:e2e     # Playwright e2e
```

## Architecture

### Request flow

React (`:5173` dev / `app/static/` prod) → FastAPI (`:8000`) → Postgres

In production: Dockerfile builds React first (`npm run build` → `app/static/`), FastAPI mounts `app/static/` at `/`. Single deployment unit on Cloud Run.

### Backend layout

```
app/
  config.py        Settings (pydantic-settings, reads .env)
  database.py      async engine + get_db() dependency; strips sslmode/channel_binding for Neon
  main.py          FastAPI lifespan: Sentry → structlog → init_db (dev) → LangGraph checkpointer
  models/          SQLModel table definitions (also used as Pydantic models)
  sources/         Job source adapters — all implement JobSource ABC from sources/base.py
  agents/          LangGraph graphs (no direct DB access — receive session via config)
  agents/test_llm.py   FakeListChatModel shim returned when ENVIRONMENT=test
  agents/llm_safe.py   safe_ainvoke() wrapper: catches ResourceExhausted → BudgetExhausted
  services/        Business logic (DB access lives here, not in agents or routers)
  services/rate_limit_service.py  Postgres-backed sliding-window rate limiter + daily quotas
  api/             FastAPI routers
  api/internal_cron.py  /internal/cron/* endpoints secured with X-Cron-Secret header
  api/status.py    GET /api/status — budget exhaustion state
  api/auth.py      fastapi-users Google OAuth (mounted only when credentials are set)
  scheduler/       Async task functions (run_job_sync, run_generation_queue, run_daily_maintenance)
```

### Three LangGraph agents

All three use `AsyncPostgresSaver` checkpointer (psycopg v3, separate connection pool from the asyncpg pool used by SQLAlchemy).

**Onboarding** (`agents/onboarding.py`): Multi-turn `StateGraph`, thread ID = `str(profile.id)`. Resumable across browser refreshes. Has a `save_profile_updates` tool that writes to DB.

**Matching** (`agents/matching_agent.py`): Fan-out via `Send` — scores N jobs in parallel. State uses `Annotated[list[ScoreResult], operator.add]` reducer to collect results from parallel branches.

**Generation** (`agents/generation_agent.py`): Parallel edges + conditional routing + `interrupt` for human review. The `interrupt` pauses execution, checkpoints state to Postgres, and resumes when the user approves/edits in the UI.

Chat streaming: `graph.astream(..., stream_mode="messages")` → `StreamingResponse(text/event-stream)`. Frontend uses `fetch()` + `ReadableStream`, not `EventSource` (POST request).

### LLM strategy

Use `langchain-google-genai` (`ChatGoogleGenerativeAI`) everywhere. No direct Google AI SDK.

Each agent module exposes `get_llm()` which returns a `FakeListChatModel` when `ENVIRONMENT=test`, otherwise `ChatGoogleGenerativeAI`. Config fields: `llm_generation_model` (gemini-2.5-pro), `llm_matching_model` (gemini-2.5-flash), `llm_resume_extraction_model` (gemini-2.5-flash).

All LLM calls should go through `safe_ainvoke()` in production to catch `ResourceExhausted`.

### Scheduler

APScheduler was removed. Scheduled work is triggered by GitHub Actions cron (`.github/workflows/cron.yml`) via HTTP `POST` to `/internal/cron/{sync,generation-queue,maintenance}` with `X-Cron-Secret` header. Use `POST /api/jobs/sync` in dev.

### Job sources

`JobSource` ABC in `app/sources/base.py`:
```python
async def search(query, location, cursor, settings, session) -> tuple[list[JobData], cursor]
```
Two live sources: `adzuna.py` and `jsearch.py`. `source_name` is stored in `jobs.source` and as the key in `user_profiles.source_cursors` JSONB. Adding a new source = implement the ABC, register in sync service.

**Adzuna enrichment** (`app/sources/adzuna_enrichment.py`): after a job is synced, `fetch_full_description(redirect_url)` fetches the detail page and scrapes salary / `contract_type` via trafilatura + bs4. Called from `job_sync_service.py`.

**Location handling**: "Remote" must never be passed as Adzuna's `where=` param — it's stripped from `target_locations` before query generation.

A `JobSearchCache` model (`app/models/search_cache.py`) deduplicates Adzuna API calls within a 24-hour window.

### Resume & matching

**Resume extraction** has two layers:
1. `app/sources/resume_parser.py` — pure text extraction (pypdf for PDF, python-docx for DOCX).
2. `app/services/resume_extraction.py` — LLM-structured extraction via Gemini Flash; called from `profile_service.py` after parsing.

**Match scoring**: `app/services/match_service.py::score_and_match` drives the matching agent and writes denormalised fields (`match_score`, `match_rationale`, `match_strengths`, `match_gaps`) onto `Application`.

### ATS submit

**Greenhouse only** supports applicant-side API submit (public board token in URL). Lever and Ashby fall back to opening the apply URL in a new tab.

`supports_api_apply = True` only when a Greenhouse board token is extractable from the URL (`app/sources/ats_detection.py`).

### Auth

Dev default (`AUTH_ENABLED=false`): `get_current_user()` returns a hardcoded user (`SINGLE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")`), auto-creating it if absent (`app/api/deps.py`).

`AUTH_ENABLED=true`: JWT is decoded directly with PyJWT (audience `fastapi-users:auth`). Google OAuth router is mounted at `/auth/google/*` when `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are set. Auth is handled by fastapi-users 15.x with `BearerTransport` + 24h JWT.

### Rate limiting

`app/services/rate_limit_service.py` — Postgres-backed via `rate_limits` table (sliding window) and `usage_counters` table (daily per-user quotas). Limits are **only enforced when `ENVIRONMENT=production`** to avoid breaking tests.

Limits: 5 profile edits/hour, 3 resume uploads/day, 1 manual sync/day.

### Key gotchas

- **SQLModel ARRAY/JSONB**: requires explicit `sa_column=Column(ARRAY(...))` / `sa_column=Column(JSONB)` — SQLModel doesn't auto-detect these.
- **Alembic model registration**: `env.py` imports from `app.models`. When adding a new model, register it in `app/models/__init__.py`.
- **Neon URL**: `sslmode=require` and `channel_binding=require` are stripped from `DATABASE_URL` in both `alembic/env.py` and `app/database.py`; `ssl=True` is passed as a `connect_arg` instead.
- **`AsyncPostgresSaver.setup()`** must run on a plain (non-pipeline) connection because it issues `CREATE INDEX CONCURRENTLY` — see `app/main.py` lifespan.
- **Matching throttle**: the matching agent uses an `asyncio.Semaphore` + 1.5s sleep between calls and 10s/30s exponential backoff on 429/rate_limit errors, falling back to `score=0.0` after retries. `ScoreResult.strengths/gaps` coerces prose to lists.
- **LangGraph checkpoint tables**: `checkpoint_*` tables are managed by `AsyncPostgresSaver.setup()`, not by Alembic. Do not add them to migrations.

### Key constraints

- `UNIQUE(source, external_id)` on `jobs` — sync is idempotent upsert
- `UNIQUE(job_id, profile_id)` on `applications` — one per user per job
- Jobs stale after 14 days without appearing in source results (`is_active = False`)
- Search auto-pauses after 7 days (`search_active = False`, `search_expires_at`)
- Max 50 work experiences per profile (enforced in `profile_service.replace_all_work_experiences`)
- Max 500 matched applications per user (trimmed by daily maintenance)
- Resume upload max 5 MB

### Frontend

Vite 5 + React 18 + TypeScript + Tailwind v3 (`tailwind.config.js` + `postcss.config.js`). Dev server proxies `/api` and `/health` to `:8000` (`frontend/vite.config.ts`). Build outputs to `app/static/` with `emptyOutDir: true`.

Playwright e2e (`frontend/e2e/`) boots FastAPI on `:8000` (with `ENVIRONMENT=test`) and Vite on `:5173`. LLM calls use `FakeListChatModel` via the test shim — no mock server needed.

Smoke tests (`tests/smoke/`) target a live server. `--has-seed-api` enables tests that rely on `POST /api/test/seed` (the dev-only router at `app/api/test_helpers.py`, mounted only when `ENVIRONMENT in ("development", "test")`).
