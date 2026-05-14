# Job Application Agent

AI-powered job search assistant: follows companies across supported ATS providers, scores each role against your profile, and queues tailored cover-letter generation.

**Live demo:** https://job-search.panibrat.com

## How it works

```
Resume upload + onboarding chat
        ↓
  Company sync across supported ATS providers
        ↓
  Worker fetches jobs and scores matches
        ↓
  Visitor reviews matches → queues a cover letter
        ↓
  "Open application" → ATS form → "Mark as applied"
```

## Tech stack

FastAPI · SQLModel · LangGraph · Google Gemini · React + Vite · Postgres (Neon) · Docker on Hetzner (managed by [panibrat-infra](https://github.com/maksym-panibrat/panibrat-infra)) · GitHub Actions.

## Quickstart

```bash
docker compose up -d db
cp .env.example .env        # set GOOGLE_API_KEY at minimum
uv sync --dev
make migrate ARGS="upgrade head"
uv run uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
# → http://localhost:5173
```

Sign-in uses Google OAuth — set `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (see `app/config.py::Settings`).

## Dev commands

```bash
uv run ruff check app/ tests/           # lint
uv run pytest tests/unit/               # fast, no DB
uv run pytest tests/integration/        # real Postgres (testcontainers)
uv run pytest tests/e2e/                # full stack
uv run pytest tests/smoke/              # live server (localhost:8000)
cd frontend && npm test                 # component tests
cd frontend && npm run build            # build to app/static/
```

`CLAUDE.md` documents the non-obvious behaviours; the directory layout is best read from the source.

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the current GHCR + Hetzner deployment flow.

## License

MIT — see [LICENSE](LICENSE).
