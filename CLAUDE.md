# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Plans — read these first

Two plan files govern this project. **Always load both at session start.**

- `~/.claude/plans/curious-crafting-narwhal.md` — primary implementation plan: full tech stack, DB schema, module specs, phases 1–9, all agent designs
- `~/.claude/plans/shimmying-seeking-teacup.md` — review session patches: 18 confirmed fixes + 3 research findings applied on top of the primary plan

The teacup file supersedes the narwhal file where they conflict.

## Development setup

```bash
# Postgres only (preferred — gives hot reload and debugger access)
docker compose up -d db
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173 (proxies /api → localhost:8000)
```

`AUTH_ENABLED=false` by default — all requests are treated as a single dev user, no login needed.

## Commands

```bash
# Lint
uv run ruff check app/ tests/

# Tests
uv run pytest tests/unit/ -v                    # fast, no DB
uv run pytest tests/integration/ -v            # requires Docker (testcontainers)
uv run pytest tests/e2e/ -v                    # full stack

# Single test
uv run pytest tests/unit/test_config.py::test_settings_defaults -v

# Migrations
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run alembic downgrade -1

# Frontend
cd frontend && npm run dev        # dev server
cd frontend && npm run build      # build to app/static/
cd frontend && npm test           # component tests (vitest)
```

Unit tests require `DATABASE_URL` and `ANTHROPIC_API_KEY` env vars to be set (can be dummy values — see existing test for pattern).

## Architecture

### Request flow

React (`:5173` dev / `app/static/` prod) → FastAPI (`:8000`) → Postgres

In production: Dockerfile builds React first (`npm run build` → `app/static/`), FastAPI mounts `app/static/` at `/`. Single deployment unit on Fly.io.

In dev: Vite dev server at `:5173` proxies `/api` and `/health` to `:8000`.

### Backend structure

```
app/
  config.py        Settings (pydantic-settings, reads .env)
  database.py      async engine + get_db() dependency
  main.py          FastAPI lifespan: Sentry → structlog → init_db (dev) → LangGraph checkpointer → APScheduler
  models/          SQLModel table definitions (also used as Pydantic models)
  schemas/         Pydantic v2 request/response contracts (separate from models)
  sources/         Job source adapters — all implement JobSource ABC from sources/base.py
  agents/          LangGraph graphs (no direct DB access — receive session via config)
  services/        Business logic (DB access lives here, not in agents or routers)
  api/             FastAPI routers
  scheduler/       APScheduler tasks
```

### Three LangGraph agents

All three use `AsyncPostgresSaver` checkpointer (psycopg v3, separate connection pool from the asyncpg pool used by SQLAlchemy).

**Onboarding** (`agents/onboarding.py`): Multi-turn `StateGraph`, thread ID = `str(profile.id)`. Resumable across browser refreshes. Has a `save_profile_updates` tool that writes to DB. Powers both initial onboarding and ongoing preference updates via the same graph.

**Matching** (`agents/matching_agent.py`): Fan-out via `Send` — scores N jobs in parallel. State uses `Annotated[list[ScoredJob], operator.add]` reducer to collect results from parallel branches. Graph: `load_context → fan_out (Send) → score_job (×N) → persist_results`.

**Generation** (`agents/generation_agent.py`): Parallel edges + conditional routing + `interrupt` for human review. Graph: `load_context → [generate_resume ‖ generate_cover_letter ‖ answer_custom_questions?] → review (interrupt) → finalize`. The `interrupt` pauses execution, checkpoints state to Postgres, and resumes when the user approves/edits in the UI. This is why LangGraph is used here — not just for sequential steps.

Chat streaming (onboarding): `graph.astream(..., stream_mode="messages", version="v2")` → `StreamingResponse(text/event-stream)`. Frontend uses `fetch()` + `ReadableStream`, not `EventSource` (POST request).

### LLM strategy

Use `langchain-anthropic` (`ChatAnthropic`) everywhere. No direct `anthropic` SDK — it's a transitive dependency only. `cache_control` on content blocks and forced `tool_choice` are both supported via `langchain-anthropic`.

- Matching/scoring: `claude-haiku-4-5-20251001` (cost-sensitive, high volume)
- Generation/onboarding: `claude-sonnet-4-6` (quality-sensitive)

Each agent module must expose a `get_llm()` factory function returning `ChatAnthropic(...)`. This is the patch point for `FakeListChatModel` in tests.

LangSmith tracing: set `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` — all LangChain/LangGraph calls auto-trace, zero instrumentation code.

### Job source pattern

`JobSource` ABC in `app/sources/base.py`:
```python
async def search(query, location, cursor, settings, session) -> tuple[list[JobData], cursor]
```
`source_name` property is the DB key stored in `jobs.source` and as the key in `user_profiles.source_cursors` JSONB. Adding a new source = implement the ABC, register in sync service. Zero changes to sync logic.

### ATS submit

**Greenhouse only** supports applicant-side API submit (public board token in URL, no employer key needed). Lever and Ashby require employer-generated API keys — they fall back to opening the apply URL in a new tab.

`supports_api_apply = True` only when a Greenhouse board token is extractable from the URL. `GreenhouseClient` in `app/sources/greenhouse.py` handles enrichment (custom questions via `?questions=true`) and submission.

### Auth

`fastapi-users` with `BearerTransport` (not cookie — avoids CSRF). Controlled by `AUTH_ENABLED` env var.

`AUTH_ENABLED=false` (dev default): `get_current_user()` in `app/api/deps.py` returns a hardcoded user (`SINGLE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")`), auto-creating it if absent.

All routers inject `profile: UserProfile = Depends(get_current_profile)`.

### Database

SQLModel models double as Pydantic models. ARRAY and JSONB columns require explicit `sa_column=Column(ARRAY(...))` / `sa_column=Column(JSONB)` — SQLModel doesn't auto-detect these.

Alembic `env.py` uses `from app.models import *` to register all models with `SQLModel.metadata`. When adding a new model, add it to `app/models/__init__.py`.

**Connection budget** (Fly.io free Postgres ≈ 20 connections):
- SQLAlchemy asyncpg: `pool_size=5, max_overflow=2`
- LangGraph checkpointer psycopg: `pool_size=3`
- APScheduler jobstore: 1 sync connection
- Total: ~11 max

### Scheduler

APScheduler 3.x (`AsyncIOScheduler`) — **pinned to `<4`**, APScheduler v4 is marked unstable in its own changelog. `SQLAlchemyJobStore` requires a sync URL — strip `+asyncpg` from `DATABASE_URL` when constructing it. APScheduler creates its own `apscheduler_jobs` table automatically; do not add it to Alembic migrations.

Three jobs: `run_job_sync` (24h), `run_generation_queue` (5min), `run_daily_maintenance` (03:00 cron). Scheduler only runs in `ENVIRONMENT=production`; local dev uses manual `POST /api/jobs/sync`.

### Postgres connection strings

The codebase uses two Postgres drivers:
- `asyncpg` for SQLAlchemy: `postgresql+asyncpg://...`
- `psycopg` (v3) for LangGraph checkpointer: `postgresql://...` (no driver suffix)

When constructing the psycopg URI from `settings.database_url`: `str(settings.database_url).replace("+asyncpg", "")`.

### Frontend

React 18, TypeScript, Vite, Tailwind CSS v3, TanStack Query v5, React Router v6. Built to `../app/static` (relative to `frontend/`). No CSS framework migration — stay on Tailwind v3.

Route structure: `/` → redirect to `/matches`, `/matches`, `/matches/:id`, `/applied`, `/profile`.

### PDF export

WeasyPrint (`markdown2` → HTML → PDF via Cairo/Pango). WeasyPrint is synchronous and CPU-bound — always wrap in `asyncio.get_event_loop().run_in_executor(None, ...)`. Requires system libraries installed in Dockerfile (use `python:3.12-bookworm`, not `-slim`).

### Key constraints

- `UNIQUE(source, external_id)` on `jobs` — sync is idempotent upsert
- `UNIQUE(job_id, profile_id)` on `applications` — one per user per job; `status IN (applied, dismissed)` excludes from future match lists
- Job stale after 14 days without appearing in source results (`is_active = False`)
- Search auto-pauses after 7 days (`search_active = False`, `search_expires_at`)
