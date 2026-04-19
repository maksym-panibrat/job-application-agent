# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development setup

```bash
docker compose up -d db
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
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

In production: Dockerfile builds React first (`npm run build` → `app/static/`), FastAPI mounts `app/static/` at `/`. Single deployment unit on Fly.io.

### Backend layout

```
app/
  config.py        Settings (pydantic-settings, reads .env)
  database.py      async engine + get_db() dependency
  main.py          FastAPI lifespan: Sentry → structlog → init_db (dev) → LangGraph checkpointer → APScheduler
  models/          SQLModel table definitions (also used as Pydantic models)
  sources/         Job source adapters — all implement JobSource ABC from sources/base.py
  agents/          LangGraph graphs (no direct DB access — receive session via config)
  services/        Business logic (DB access lives here, not in agents or routers)
  api/             FastAPI routers
  scheduler/       APScheduler tasks
```

### Three LangGraph agents

All three use `AsyncPostgresSaver` checkpointer (psycopg v3, separate connection pool from the asyncpg pool used by SQLAlchemy).

**Onboarding** (`agents/onboarding.py`): Multi-turn `StateGraph`, thread ID = `str(profile.id)`. Resumable across browser refreshes. Has a `save_profile_updates` tool that writes to DB.

**Matching** (`agents/matching_agent.py`): Fan-out via `Send` — scores N jobs in parallel. State uses `Annotated[list[ScoreResult], operator.add]` reducer to collect results from parallel branches.

**Generation** (`agents/generation_agent.py`): Parallel edges + conditional routing + `interrupt` for human review. The `interrupt` pauses execution, checkpoints state to Postgres, and resumes when the user approves/edits in the UI.

Chat streaming: `graph.astream(..., stream_mode="messages")` → `StreamingResponse(text/event-stream)`. Frontend uses `fetch()` + `ReadableStream`, not `EventSource` (POST request).

### LLM strategy

Use `langchain-anthropic` (`ChatAnthropic`) everywhere. No direct `anthropic` SDK.

Each agent module exposes `get_llm()` returning `ChatAnthropic(...)`. This is the patch point for mocking in tests.

`ANTHROPIC_BASE_URL` redirects all LLM calls to a mock server — how Playwright e2e and smoke tests avoid real API calls.

### Job sources

`JobSource` ABC in `app/sources/base.py`:
```python
async def search(query, location, cursor, settings, session) -> tuple[list[JobData], cursor]
```
Two live sources: `adzuna.py` and `jsearch.py`. `source_name` is stored in `jobs.source` and as the key in `user_profiles.source_cursors` JSONB. Adding a new source = implement the ABC, register in sync service.

**Adzuna enrichment** (`app/sources/adzuna_enrichment.py`): after a job is synced, `fetch_full_description(redirect_url)` fetches the detail page and scrapes salary / `contract_type` via trafilatura + bs4. Called from `job_sync_service.py`.

A `JobSearchCache` model (`app/models/search_cache.py`) deduplicates Adzuna API calls within a 24-hour window.

### Resume & matching

**Resume extraction** has two layers:
1. `app/sources/resume_parser.py` — pure text extraction (pypdf for PDF, python-docx for DOCX).
2. `app/services/resume_extraction.py` — LLM-structured extraction via Haiku; called from `profile_service.py` after parsing.

**Match scoring**: `app/services/match_service.py::score_and_match` drives the matching agent and writes denormalised fields (`match_score`, `match_rationale`, `match_strengths`, `match_gaps`) onto `Application`.

### ATS submit

**Greenhouse only** supports applicant-side API submit (public board token in URL). Lever and Ashby fall back to opening the apply URL in a new tab.

`supports_api_apply = True` only when a Greenhouse board token is extractable from the URL (`app/sources/ats_detection.py`).

### Auth

Dev default (`AUTH_ENABLED=false`): `get_current_user()` returns a hardcoded user (`SINGLE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")`), auto-creating it if absent (`app/api/deps.py`). `AUTH_ENABLED=true` is not yet implemented (raises 501).

### Key gotchas

- **SQLModel ARRAY/JSONB**: requires explicit `sa_column=Column(ARRAY(...))` / `sa_column=Column(JSONB)` — SQLModel doesn't auto-detect these.
- **Alembic model registration**: `env.py` imports from `app.models`. When adding a new model, register it in `app/models/__init__.py`.
- **APScheduler jobstore**: requires a sync URL — strip `+asyncpg` from `DATABASE_URL`. APScheduler creates its own `apscheduler_jobs` table; do not add it to Alembic migrations. Pinned to `<4` (v4 unstable).
- **`AsyncPostgresSaver.setup()`** must run on a plain (non-pipeline) connection because it issues `CREATE INDEX CONCURRENTLY` — see `app/main.py` lifespan.
- **Matching throttle**: the matching agent uses a `threading.Semaphore` + 1.5s sleep between calls and 10s/30s exponential backoff on 429, falling back to `score=0.0` after retries. Haiku sometimes returns prose instead of lists; `ScoreResult.strengths/gaps` coerces those to lists.
- **Scheduler**: only runs in `ENVIRONMENT=production`. Use `POST /api/jobs/sync` in dev. Three jobs: 24h sync, 5-min generation queue, daily 03:00 maintenance.

### Key constraints

- `UNIQUE(source, external_id)` on `jobs` — sync is idempotent upsert
- `UNIQUE(job_id, profile_id)` on `applications` — one per user per job
- Jobs stale after 14 days without appearing in source results (`is_active = False`)
- Search auto-pauses after 7 days (`search_active = False`, `search_expires_at`)

### Frontend

Vite 5 + React 18 + TypeScript + Tailwind v3 (`tailwind.config.js` + `postcss.config.js`). Dev server proxies `/api` and `/health` to `:8000` (`frontend/vite.config.ts`). Build outputs to `app/static/` with `emptyOutDir: true`.

Playwright e2e (`frontend/e2e/`) boots three serial webServers: a mock LLM on `:9000`, FastAPI on `:8000` (with `ANTHROPIC_BASE_URL=http://localhost:9000`), and Vite on `:5173` (`frontend/playwright.config.ts`).

Smoke tests (`tests/smoke/`) target a live server. `--has-seed-api` enables tests that rely on `POST /api/test/seed` (the dev-only router at `app/api/test_helpers.py`, conditionally mounted in `app/main.py`).
