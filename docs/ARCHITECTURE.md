# Architecture

This app helps a user follow target companies, fetch open roles from supported ATS boards, score those roles against a profile, and generate a cover letter on demand.

Docs should explain why the system is shaped this way. Code is the source of truth for exact routes, statuses, config names, schemas, prompts, and job payloads.

## Design principles

- Keep request handlers quick. Public APIs should validate, persist intent, enqueue work when needed, and return.
- Put slow or failure-prone work behind the durable Postgres queue: ATS fetches, LLM matching, cover-letter generation, reconciliation, and maintenance.
- Prefer deterministic filtering before LLM calls. Spend model tokens only when the app has enough structured context to make the call useful.
- Keep LLM correlation boring. Local IDs and provider request keys should drive imports; never rely on a model copying identifiers correctly.
- Avoid hidden schedulers. Cron lives outside the app process and calls thin internal endpoints that enqueue work.
- Treat production operations as infrastructure concerns. Host runtime, Caddy, secrets, rollback, cron schedules, and observability live in `panibrat-infra`.

## Runtime boundary

Production is split into:

- API process: FastAPI serving JSON APIs and the built frontend.
- Worker process: same image, consuming the Postgres-backed work queue.
- Postgres: durable application state and queue state.
- External scheduler: calls internal cron enqueue endpoints.
- Infrastructure repo: compose, deploy/rollback, host secrets, proxying, and log shipping.

There is intentionally no in-process scheduler. If a task can outlive a web request or can fail independently, enqueue it and let the worker own retries and finalization.

## Product flow

```text
Sign in
  -> create/update profile from resume and onboarding chat
  -> choose companies to follow
  -> sync jobs from supported ATS providers
  -> create application candidates
  -> match each candidate against the profile
  -> review matches
  -> generate a cover letter on demand
  -> apply on the ATS and mark the app state locally
```

## Code map

Use these paths to answer “what exactly does it do today?”

- `app/main.py`: app setup and route registration.
- `app/config.py`: environment-backed settings.
- `app/api/`: HTTP route contracts.
- `app/models/`: database tables and persisted enums.
- `app/services/`: business logic, provider wrappers, orchestration.
- `app/agents/`: LLM prompts, structured outputs, and invocation safety.
- `app/sources/`: ATS adapters.
- `app/worker/`: queue claiming, retries, leases, handlers, finalization.
- `app/scheduler/tasks.py`: cron-triggered enqueue helpers.
- `frontend/src/api/`: frontend API client/types.
- `frontend/src/pages/` and `frontend/src/components/`: user-facing flows.

## LLM boundaries

Matching and generation are intentionally asynchronous because provider calls are slow, expensive, and quota-limited. The app should record user intent first, then let the worker run the model call and persist the result.

Onboarding is the exception that keeps conversational state: it uses LangGraph checkpointing because a profile-building chat needs resumable state. Matching and generation do not need graph checkpoints; their durable state is normal application data plus queue rows.

## Database and migrations

Use the migration wrapper (`make migrate ARGS="..."` or `scripts/alembic_safe.py`) instead of raw Alembic for write operations. The wrapper exists to reduce the chance of accidentally advancing a production-like database from a local checkout.

SQLModel does not infer every Postgres type. When using Postgres-specific columns such as arrays or JSONB, define the SQLAlchemy column explicitly and ensure new models are imported from `app/models/__init__.py` so Alembic can see them.

## Documentation rule

Keep this file durable. Do not list every endpoint, enum value, queue type, prompt field, or deployment step here unless the detail explains an architectural decision. Link to code for current mechanics and to runbooks for operational procedures.
