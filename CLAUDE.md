# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
uv run pytest tests/path/to/test.py::test_name -v

# Migrations
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run alembic downgrade -1

# Frontend
cd frontend && npm run dev        # dev server
cd frontend && npm run build      # build to app/static/
cd frontend && npm test           # component tests (vitest)
```

## Architecture

### Request flow

React (`:5173` dev / `app/static/` prod) → FastAPI (`:8000`) → Postgres

In production: Dockerfile builds React first (`npm run build` → `app/static/`), FastAPI mounts `app/static/` at `/`. Single deployment unit on Fly.io.

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

**Onboarding** (`agents/onboarding.py`): Multi-turn `StateGraph`, thread ID = `str(profile.id)`. Resumable across browser refreshes. Has a `save_profile_updates` tool that writes to DB.

**Matching** (`agents/matching_agent.py`): Fan-out via `Send` — scores N jobs in parallel. State uses `Annotated[list[ScoredJob], operator.add]` reducer to collect results from parallel branches.

**Generation** (`agents/generation_agent.py`): Parallel edges + conditional routing + `interrupt` for human review. The `interrupt` pauses execution, checkpoints state to Postgres, and resumes when the user approves/edits in the UI.

Chat streaming: `graph.astream(..., stream_mode="messages", version="v2")` → `StreamingResponse(text/event-stream)`. Frontend uses `fetch()` + `ReadableStream`, not `EventSource` (POST request).

### LLM strategy

Use `langchain-anthropic` (`ChatAnthropic`) everywhere. No direct `anthropic` SDK — it's a transitive dependency only.

Each agent module exposes a `get_llm()` factory function returning `ChatAnthropic(...)`. This is the patch point for mocking in tests.

### Job source pattern

`JobSource` ABC in `app/sources/base.py`:
```python
async def search(query, location, cursor, settings, session) -> tuple[list[JobData], cursor]
```
`source_name` is stored in `jobs.source` and as the key in `user_profiles.source_cursors` JSONB. Adding a new source = implement the ABC, register in sync service.

### ATS submit

**Greenhouse only** supports applicant-side API submit (public board token in URL). Lever and Ashby fall back to opening the apply URL in a new tab.

`supports_api_apply = True` only when a Greenhouse board token is extractable from the URL.

### Auth

`fastapi-users` with `BearerTransport`. `AUTH_ENABLED=false` (dev default): `get_current_user()` returns a hardcoded user (`SINGLE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")`), auto-creating it if absent.

### Key gotchas

- **SQLModel ARRAY/JSONB**: requires explicit `sa_column=Column(ARRAY(...))` / `sa_column=Column(JSONB)` — SQLModel doesn't auto-detect these.
- **Alembic model registration**: `env.py` uses `from app.models import *`. When adding a new model, add it to `app/models/__init__.py`.
- **APScheduler jobstore**: requires a sync URL — strip `+asyncpg` from `DATABASE_URL`. APScheduler creates its own `apscheduler_jobs` table; do not add it to Alembic migrations. Pinned to `<4` (v4 is unstable).
- **WeasyPrint**: synchronous and CPU-bound — always wrap in `run_in_executor`. Requires system libs (Cairo/Pango) — Dockerfile uses `python:3.12-bookworm`, not `-slim`.
- **Scheduler**: only runs in `ENVIRONMENT=production`. Local dev uses manual `POST /api/jobs/sync`.

### Key constraints

- `UNIQUE(source, external_id)` on `jobs` — sync is idempotent upsert
- `UNIQUE(job_id, profile_id)` on `applications` — one per user per job
- Job stale after 14 days without appearing in source results (`is_active = False`)
- Search auto-pauses after 7 days (`search_active = False`, `search_expires_at`)
