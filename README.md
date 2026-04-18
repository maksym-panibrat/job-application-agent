# Job Application Agent

AI-powered job search automation: monitors job boards, scores matches against your profile, and pre-generates tailored resume and cover letter before you even open the listing.

## How it works

```
Resume upload + onboarding chat
        ↓
  Adzuna job sync  ──→  ATS detection (Greenhouse / Lever / Ashby)
        ↓
  Matching agent scores each job against your profile
        ↓
  Generation agent produces tailored resume + cover letter
  (runs in background — documents ready when you open the match card)
        ↓
  Review + edit inline → Approve → Submit
  (Greenhouse: API submit · Others: open apply URL)
```

## Key features

- **Conversational onboarding**: chat agent asks about target roles, location, preferences; updates your profile via tool calls; persists conversation state across sessions (LangGraph + PostgresSaver)
- **Parallel job scoring**: LangGraph `Send` fan-out scores multiple jobs concurrently; results collected via state reducer
- **Human-in-the-loop generation**: generation graph pauses after producing documents; resumes when you approve or edit in the UI
- **Search auto-pause**: job search pauses after 7 days to cap API costs; one-click resume

## Quickstart

```bash
docker compose up -d db
cp .env.example .env        # set ANTHROPIC_API_KEY at minimum
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173
```

No login required locally (`AUTH_ENABLED=false`).

## Dev commands

```bash
uv run ruff check app/ tests/           # lint
uv run pytest tests/unit/ -v            # fast, no DB
uv run pytest tests/integration/ -v    # real Postgres (testcontainers)
uv run pytest tests/e2e/ -v             # full stack
uv run pytest tests/smoke/ -v          # against live server (localhost:8000)
uv run pytest tests/smoke/ -v --has-seed-api  # smoke with pre-seeded data
cd frontend && npm test                 # component tests
cd frontend && npm run build            # build to app/static/
```

## Project structure

```
app/
  agents/      LangGraph graphs — onboarding, matching, generation
  api/         FastAPI routers
  models/      SQLModel table definitions
  services/    Business logic
  sources/     Job source adapters + ATS detection + resume parser
  scheduler/   APScheduler tasks (24h sync, 5m gen queue, daily maintenance)
frontend/
  src/pages/   Matches, ApplicationReview, Onboarding (chat), Applied
tests/
  unit/        Pure Python, no I/O
  integration/ Real Postgres via testcontainers
  e2e/         Full FastAPI stack via httpx
  smoke/       Live HTTP smoke tests (requires running server)
```

See `.env.example` for all configuration options and `CLAUDE.md` for architecture notes.

## License

MIT — see [LICENSE](LICENSE).
