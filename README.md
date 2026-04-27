# Job Application Agent

AI-powered job search assistant: ingests Greenhouse company boards, scores each role against your profile, and generates a tailored cover letter on demand.

**Live demo:** https://api-2twfzafrta-uc.a.run.app

## How it works

```
Resume upload + onboarding chat
        ↓
  Greenhouse public board sync (target_company_slugs)
        ↓
  Matching agent scores each job (Gemini Flash, parallel Send fan-out)
        ↓
  Visitor reviews matches → clicks "Generate cover letter" (Gemini Pro, ~15s)
        ↓
  "Open application" → Greenhouse form → "Mark as applied"
```

## Key features

- **Conversational onboarding** — chat agent asks about target roles, location, preferences; updates your profile via tool calls; persists conversation state across browser sessions (LangGraph + AsyncPostgresSaver, onboarding only)
- **Parallel job scoring** — LangGraph `Send` fan-out scores multiple jobs concurrently; results collected via state reducer
- **Synchronous cover-letter generation** — `POST /api/applications/{id}/cover-letter` runs the linear graph in-request; `none → generating → ready/failed`
- **Externalised scheduler** — no in-process scheduler; GitHub Actions cron hits `/internal/cron/*` endpoints (compatible with Cloud Run scale-to-zero)
- **Budget safety** — Gemini `ResourceExhausted` errors are caught, stored in `llm_status`, surfaced as an amber banner; job collection keeps running
- **Rate limiting** — Postgres-backed sliding-window limits on profile edits, resume uploads, and manual syncs; per-user daily quotas (production only)
- **Observability** — errors flow to GCP Cloud Error Reporting via structlog (`severity=ERROR` + `@type: …ReportedErrorEvent` markers); no third-party SaaS

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI 0.115, SQLModel, asyncpg |
| LLM | Google Gemini 2.5 Flash / Pro via `langchain-google-genai` |
| Agent framework | LangGraph 0.2 |
| Database | Neon Postgres (free tier) |
| Hosting | Google Cloud Run (free tier, scale-to-zero) |
| Frontend | React 18 + TypeScript + Vite + Tailwind v3 |
| CI/CD | GitHub Actions — test → build → deploy pipeline |

## Quickstart

```bash
docker compose up -d db
cp .env.example .env        # set GOOGLE_API_KEY at minimum
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173
```

Google OAuth is required: set `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (see `app/config.py::Settings`).

## Dev commands

```bash
uv run ruff check app/ tests/           # lint
uv run pytest tests/unit/ -v            # fast, no DB
uv run pytest tests/integration/ -v    # real Postgres (testcontainers)
uv run pytest tests/e2e/ -v             # full stack
uv run pytest tests/smoke/ -v          # against live server (localhost:8000)
cd frontend && npm test                 # component tests
cd frontend && npm run build            # build to app/static/
```

## Project structure

```
app/
  agents/      LangGraph graphs (onboarding, matching, generation) + test shim
  api/         FastAPI routers — profile, jobs, applications, chat, cron, auth, status
  models/      SQLModel table definitions
  services/    Business logic (job sync, matching, generation, rate limiting)
  sources/     Job source adapters (Greenhouse Board) + resume parser
  scheduler/   Async task functions (called by cron endpoints)
frontend/
  src/
    pages/     Matches, ApplicationReview, Onboarding (chat), Applied, Landing
    context/   AuthProvider (Google OAuth token management)
    components/ BudgetBanner, RequireAuth
tests/
  unit/        Pure Python, no I/O
  integration/ Real Postgres via testcontainers (includes data isolation tests)
  e2e/         Full FastAPI stack via httpx
  smoke/       Live HTTP smoke tests (requires running server)
.github/
  workflows/
    ci.yml     test → frontend → e2e-browser → migrate → deploy (main only)
    cron.yml   GitHub Actions cron hitting /internal/cron/* endpoints
```

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full GCP + Neon provisioning guide.

## License

MIT — see [LICENSE](LICENSE).
