# CLAUDE.md

Guidance for Claude Code working in this repo. Keep only non-obvious behaviors; look up anything else in the code.

## Setup

```bash
docker compose up -d db
uv sync --dev
make migrate ARGS="upgrade head"                # wraps alembic; refuses non-local DATABASE_URL without I_KNOW_ITS_PROD=1
uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev   # :5173, proxies /api + /health to :8000
```

No tracked `.env.example`. Required env: `DATABASE_URL`, `GOOGLE_API_KEY`. Full list: `app/config.py::Settings`.

`AUTH_ENABLED=false` (default) treats every request as a single hardcoded user (`app/api/deps.py::SINGLE_USER_ID`).

**Migrations**: always use `make migrate ARGS="..."` (or `uv run python scripts/alembic_safe.py ...`), never plain `alembic`. The wrapper blocks write commands (`upgrade` / `downgrade` / `stamp` / `merge` / `revision --autogenerate`) against non-local hosts unless `I_KNOW_ITS_PROD=1` is set. Prod migrations belong to the `migrate` CI job (`.github/workflows/ci.yml`), not a dev laptop — running `alembic upgrade head` against Neon locally is the exact outage mode of commit 28e5ce5 (schema ahead of deployed code → every `select(Application)` 500s).

## Tests

```bash
uv run pytest tests/unit/           # fast, no DB
uv run pytest tests/integration/    # testcontainers Postgres
uv run pytest tests/e2e/            # full stack
uv run pytest tests/smoke/ --has-seed-api   # live server at :8000
```

`--has-seed-api` enables tests that call `POST /api/test/seed` — mounted only when `ENVIRONMENT in ("development","test")`.

Each agent module's `get_llm()` returns `FakeListChatModel` when `ENVIRONMENT=test`, so no real API key is needed.

## Non-obvious behaviors

### LangGraph checkpointer

Onboarding (`app/agents/onboarding.py`) is the only agent that uses `AsyncPostgresSaver` — on a **separate psycopg v3 pool** from the SQLAlchemy asyncpg pool. `setup()` must run on a plain (non-pipeline) connection — it issues `CREATE INDEX CONCURRENTLY`. `checkpoint_*` tables are owned by the saver; **do not add them to Alembic migrations**. Generation and matching agents do not checkpoint. All LLM calls go through `safe_ainvoke()` (`app/agents/llm_safe.py`) to catch `ResourceExhausted`.

### SQLModel / Alembic / Neon

- SQLModel does NOT auto-detect ARRAY/JSONB — use explicit `sa_column=Column(ARRAY(...))` / `sa_column=Column(JSONB)`.
- Register new models in `app/models/__init__.py` so `alembic/env.py` sees them.
- Neon: `sslmode` / `channel_binding` are stripped from `DATABASE_URL` in `alembic/env.py` and `app/database.py`; `ssl=True` is passed as a `connect_arg` instead.

### Job sourcing

- New listing source: implement `JobSource` ABC (`app/sources/base.py`), register in `app/services/job_sync_service.py`, add an enable flag on `Settings`.
- **Never** pass `"Remote"` as Adzuna's `where=` param — strip it from `target_locations` first.
- `JobSearchCache` dedupes Adzuna calls within 24h.
- `supports_api_apply = True` only when a Greenhouse public board token is extractable (`app/sources/ats_detection.py`); everything else falls back to opening the apply URL.

### Rate limiting

`rate_limit_service.py` is only enforced when `settings.environment == "production"` (guard at the API layer — e.g. `app/api/profile.py`, `app/api/jobs.py`). Tests would break otherwise.

### Matching throttle

Matching agent uses `asyncio.Semaphore` + 1.5s sleep + 10s/30s exponential backoff on 429; falls back to `score=0.0` after retries. `ScoreResult.strengths/gaps` coerces prose to lists.

### Scheduler

No in-process scheduler. `app/scheduler/tasks.py` is invoked via `POST /internal/cron/{sync,generation-queue,maintenance}` with `X-Cron-Secret`, triggered by `.github/workflows/cron.yml`.

### Generation contract

`generate_materials()` (`app/services/application_service.py`) runs the cover-letter graph synchronously and returns the saved `GeneratedDocument`. The HTTP entrypoint is `POST /api/applications/{id}/cover-letter` — the request blocks for the duration of the LLM call (≈10–30s) and there is no background task, no checkpointer, no interrupt, and no `/resume` endpoint. Valid `generation_status` values: `none · generating · ready · failed`. Single-writer rule: `generate_materials` owns the status writes (`generating` → `ready`/`failed`); the API route does nothing but await it.

### Observability

No Sentry / no external SaaS. Errors flow to GCP Cloud Error Reporting via structlog: `app/main.py::_add_cloud_run_severity` injects `severity=ERROR` + `@type: …ReportedErrorEvent`, and `structlog.processors.format_exc_info` turns `exc_info=True` / `log.aexception` into a readable traceback. `gcloud services enable clouderrorreporting.googleapis.com` is a one-time op per project.

## Hard app-level limits (not DB constraints)

50 work experiences/profile · 500 matched applications/user · 5 MB resume · 14-day job staleness · 7-day search auto-pause.
