# Job Application Agent

An AI-powered job application agent built as a portfolio piece demonstrating production-grade agentic AI patterns: LangGraph state machines, human-in-the-loop review, multi-source job discovery, and automated resume tailoring.

## Architecture

```mermaid
graph TD
    subgraph Frontend["React Frontend (Vite + Tailwind)"]
        UI[Matches / Review / History / Profile]
        Chat[Chat UI - SSE stream]
    end

    subgraph API["FastAPI Backend"]
        Jobs[/api/jobs]
        Apps[/api/applications]
        Docs[/api/documents]
        Prof[/api/profile]
        ChatAPI[/api/chat/messages]
    end

    subgraph Agents["LangGraph Agents"]
        Onboard[Onboarding Agent<br/>StateGraph + AsyncPostgresSaver<br/>SSE streaming]
        Match[Matching Agent<br/>Send fan-out — N jobs in parallel<br/>Haiku for cost efficiency]
        Gen[Generation Agent<br/>Parallel edges + interrupt<br/>Human-in-the-loop resume review]
    end

    subgraph Sources["Job Sources"]
        Adzuna[Adzuna API<br/>cursor-paginated]
        GH[Greenhouse<br/>API submit]
        ATS[Lever / Ashby<br/>URL detect only]
    end

    subgraph Infra["Infrastructure"]
        PG[(PostgreSQL)]
        Sched[APScheduler<br/>24h sync / 5m gen queue]
        Sentry[Sentry]
        LS[LangSmith]
    end

    UI --> Jobs & Apps & Docs & Prof
    Chat --> ChatAPI
    ChatAPI --> Onboard
    Jobs --> Sources
    Adzuna --> Match
    Match --> Gen
    Onboard & Match & Gen --> PG
    Onboard & Match & Gen --> LS
    Sched --> Jobs
    API --> Sentry
```

## Key Features

- **Conversational onboarding**: LangGraph `StateGraph` with `AsyncPostgresSaver` — conversation persists across browser refreshes
- **Parallel job scoring**: `Send`-based fan-out scores N jobs concurrently; `Annotated[list, operator.add]` reducer collects results
- **Human-in-the-loop generation**: Generation agent pauses with `interrupt()` after producing resume + cover letter; resumes when user approves/edits in the UI
- **ATS-aware submission**: Greenhouse jobs submit via board API; Lever/Ashby fall back to opening the apply URL
- **Search auto-pause**: Job search pauses after 7 days to prevent runaway API costs on forgotten deployments
- **SSE streaming**: Chat responses stream token-by-token; generation status pushes "ready" without polling
- **PDF export**: WeasyPrint renders tailored resume to PDF; runs in `run_in_executor` to avoid blocking the event loop

## Tech Stack

| Layer | Choice |
|---|---|
| LLM | `langchain-anthropic` — Sonnet for generation/onboarding, Haiku for scoring |
| Agent orchestration | LangGraph (`StateGraph`, `Send` fan-out, `interrupt`) |
| Tracing | LangSmith — auto-traces all LangChain calls, zero instrumentation |
| Backend | FastAPI + SQLModel + asyncpg + Alembic |
| Scheduler | APScheduler 3.x `AsyncIOScheduler` |
| Frontend | React 18 + TypeScript + Vite + Tailwind v3 + TanStack Query v5 |
| Hosting | Fly.io (single deployment unit — React built into FastAPI static) |

## Quickstart

```bash
# 1. Start Postgres
docker compose up -d db

# 2. Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# 3. Install + migrate
uv sync --dev
uv run alembic upgrade head

# 4. Start backend
uv run uvicorn app.main:app --reload --port 8000

# 5. Start frontend (separate terminal)
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```

`AUTH_ENABLED=false` by default — no login needed locally.

## Development Commands

```bash
# Lint
uv run ruff check app/ tests/

# Tests
uv run pytest tests/unit/ -v                 # fast, no DB
uv run pytest tests/integration/ -v         # real Postgres via testcontainers
uv run pytest tests/e2e/ -v                  # full stack

# Frontend
cd frontend && npm test                       # component tests (vitest)
cd frontend && npm run build                  # build to app/static/

# Migrations
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## Project Structure

```
app/
  agents/          LangGraph agents (no direct DB access)
    onboarding.py  Multi-turn chat with PostgresSaver checkpointer
    matching_agent.py  Send fan-out scoring
    generation_agent.py  Parallel generation + interrupt for review
  api/             FastAPI routers
  models/          SQLModel table definitions
  services/        Business logic (DB access lives here)
  sources/         Job source adapters (JobSource ABC)
    adzuna.py      Primary job discovery
    greenhouse.py  ATS enrichment + API submit
    ats_detection.py  URL-based ATS type detection
  scheduler/       APScheduler tasks
frontend/
  src/pages/
    Matches.tsx        Job match feed with score badges
    ApplicationReview.tsx  Inline editor + approve/dismiss
    Onboarding.tsx     Chat UI + resume upload
    Applied.tsx        Application history
tests/
  unit/            Pure Python, no I/O
  integration/     Real Postgres via testcontainers
  e2e/             Full FastAPI stack
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | `postgresql+asyncpg://...` |
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key |
| `ADZUNA_APP_ID` / `ADZUNA_API_KEY` | No | — | Job discovery (skipped gracefully if unset) |
| `ENVIRONMENT` | No | `development` | Set to `production` on Fly.io |
| `AUTH_ENABLED` | No | `false` | OAuth login (disabled locally) |
| `LANGCHAIN_TRACING_V2` | No | `false` | Enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | No | — | Required if tracing enabled |
| `SENTRY_DSN` | No | — | Error tracking (disabled if unset) |
| `TAVILY_API_KEY` | No | — | Company context enrichment |

## Deployment (Fly.io)

```bash
fly launch --no-deploy    # first time only — creates fly.toml + Postgres
fly secrets set ANTHROPIC_API_KEY=... ADZUNA_APP_ID=... ADZUNA_API_KEY=... JWT_SECRET=...
fly deploy
```

Fly runs `alembic upgrade head` before each deploy (`release_command` in `fly.toml`). `auto_stop_machines = false` keeps the APScheduler alive for periodic syncs.

## License

MIT — see [LICENSE](LICENSE).
