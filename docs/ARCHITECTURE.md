# Architecture

This app helps a user follow target companies, fetch open roles from supported ATS boards, score those roles against a profile, and generate a cover letter on demand.

## Runtime

Production is split across this app repo and the `panibrat-infra` repo.

- `job-search-api`: FastAPI app serving the API and built frontend.
- `job-search-worker`: same image, running `python -m app.worker`.
- Postgres: self-hosted on the Hetzner host.
- `supercronic`: external scheduler that calls internal cron endpoints.
- Vector ships container logs to Axiom.

There is no in-process scheduler. Cron endpoints enqueue work only; workers do the long-running fetch, match, generation, and maintenance tasks.

## Product flow

```text
Google sign-in
  -> profile/resume/onboarding chat
  -> followed companies
  -> job sync fetches ATS postings
  -> worker creates Application rows
  -> async matching scores each Application
  -> user reviews matches
  -> user requests cover letter
  -> worker generates document
  -> user opens ATS form and marks applied
```

## Backend layout

- `app/api/`: FastAPI routes. Public endpoints should stay quick and return `202` for long-running work.
- `app/services/`: deterministic business logic, database orchestration, provider wrappers.
- `app/agents/`: LLM prompt/response code. Onboarding is the only graph with checkpointing.
- `app/sources/`: ATS provider adapters.
- `app/models/`: SQLModel table definitions. Alembic owns schema changes.
- `app/worker/`: Postgres-backed queue processor and job handlers.
- `app/scheduler/tasks.py`: cron-triggered enqueue and maintenance helpers.

## Work queue

`work_queue` is the durable async boundary. Rows are claimed with Postgres locks, leases, retry backoff, and dedupe keys.

Current job types:

- `fetch-slug`: fetch one provider slug and upsert jobs/applications.
- `match`: score one application synchronously through the normal matching path.
- `batch-match`: submit/poll/import async provider-batch matching for one application per provider request.
- `generate-cover-letter`: create or regenerate a cover letter for one application.
- `maintenance`: run daily cleanup/search-expiry tasks.

The public API and cron routes enqueue these jobs. They should not do provider fetches or LLM calls inline.

## Matching

Matching has two layers:

1. Deterministic filters reject obvious mismatches before spending LLM tokens.
2. The LLM scores one application/job against the profile and returns structured score fields.

The batch API is used as an async transport, not as a multi-job prompt. Each provider request contains one local application/job pair so response correlation stays boring and predictable.

## Generation

Cover-letter generation is asynchronous:

- `POST /api/applications/{id}/cover-letter` sets `generation_status=pending`, enqueues work, and returns `202`.
- Worker moves status through `pending -> generating -> ready` or `failed`.
- Clients poll `GET /api/applications/{id}/cover-letter/status`.

Valid generation statuses are `none`, `pending`, `generating`, `ready`, and `failed`.

## Onboarding and checkpoints

Onboarding uses LangGraph plus `AsyncPostgresSaver` because chat state needs checkpointing. The checkpointer uses a separate psycopg v3 pool from the SQLAlchemy asyncpg pool. The `checkpoint_*` tables are owned by LangGraph and must not be added to Alembic migrations.

Generation and matching do not need checkpoints.

## Database and migrations

Use `make migrate ARGS="..."` or `uv run python scripts/alembic_safe.py ...`; never run raw `alembic` for write migrations. The wrapper blocks accidental writes against non-local production-like databases unless explicitly overridden.

SQLModel does not infer every Postgres type. ARRAY and JSONB fields should use explicit `sa_column=Column(...)`. New models must be imported from `app/models/__init__.py` so Alembic sees them.

## Frontend layout

- `frontend/src/pages/`: page-level routes.
- `frontend/src/components/`: reusable UI and page sections.
- `frontend/src/api/`: API client/types.
- `frontend/src/test/`: MSW and test setup.

The frontend polls only while user-visible async work is active. Avoid background minute loops per component.
