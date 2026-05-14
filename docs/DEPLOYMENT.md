# Deployment Reference

Production runs on the Hetzner host managed by
[`panibrat-infra`](https://github.com/maksym-panibrat/panibrat-infra). This app
repo builds and publishes the image; the infra repo owns compose, Caddy,
supercronic, Vector, release migrations, rollback, and host secrets.

## Runtime Shape

- `job-search-api`: FastAPI serving `job-search.panibrat.com`.
- `job-search-worker`: same image, `python -m app.worker`, consuming Postgres
  `work_queue`.
- `alembic-upgrade`: release-profile compose service run during deploy.
- `supercronic`: external scheduler that calls thin internal cron enqueuers.
- Neon Postgres remains external.
- Logs ship through Vector to Axiom.

## Normal Deploy Flow

1. Push to `main`.
2. `ci.yml` runs backend, frontend, browser E2E, then builds the Docker image.
3. The image is pushed to GHCR as both `:<commit-sha>` and `:main`.
4. CI sends a `bump-app-image` repository dispatch to `panibrat-infra` with
   `app=job-search` and the commit SHA.
5. `panibrat-infra/.github/workflows/bump.yml` opens a one-line `compose.yml`
   bump PR.
6. Merging that PR triggers `panibrat-infra/.github/workflows/deploy.yml`.
7. The deploy script SSHes to the host, pulls the image, pauses supercronic,
   runs Alembic through the release profile, starts API and worker, verifies
   health, reloads Caddy, and resumes supercronic unless it was operator-paused.

The active operational runbooks live in `panibrat-infra/docs/runbooks/`,
especially `deploy.md`, `rollback.md`, `cron.md`, and `observability.md`.

## Required GitHub Secrets

| Secret | Used by | Purpose |
|---|---|---|
| `INFRA_DISPATCH_TOKEN` | `ci.yml` | Allows this repo to dispatch the image bump into `panibrat-infra`. |
| `GITHUB_TOKEN` | GitHub Actions | Publishes package images to GHCR. |

Application runtime secrets are not stored in this repo. They live on the
Hetzner box under `/srv/job-search/.env` and are restored/rotated through the
infra repo procedures.

## Worker Queue Contract

The public API avoids long-running LLM/fetch work:

- `POST /api/jobs/sync` returns `202`, prunes invalid followed companies,
  enqueues stale provider slugs, and synchronously scores only cached jobs for
  quick UI feedback.
- `POST /api/applications/{id}/cover-letter` returns `202`, flips the
  application to `pending`, and enqueues `generate-cover-letter`.
- Clients poll `GET /api/applications/{id}/cover-letter/status` while the
  worker moves generation through `pending -> generating -> ready/failed`.

Cron endpoints are protected by `X-Cron-Secret` and enqueue work only:

- `POST /internal/cron/sync` enqueues stale `fetch-slug` jobs.
- `POST /internal/cron/generation-reconcile` re-enqueues orphaned pending cover
  letters.
- `POST /internal/cron/maintenance` enqueues one daily maintenance job.

`app.worker` owns queue claiming, lease timeouts, retries, terminal failure
handling, and finalization.

## Local Verification Before Merge

```bash
uv run ruff check app/ tests/
uv run pytest tests/unit/
uv run pytest tests/integration/
cd frontend && npm test && npm run build
```

For a full local stack, run the frontend dev server and API separately as shown
in the README. Production verification after deploy belongs in
`panibrat-infra` because that repo has the host, compose, Caddy, and Axiom
context.
