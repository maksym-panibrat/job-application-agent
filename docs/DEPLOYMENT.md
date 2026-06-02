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
- Self-hosted Postgres runs on the Hetzner host.
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

The queue contract is documented in [`ARCHITECTURE.md`](ARCHITECTURE.md). In short: public API and cron routes enqueue work, and `app.worker` owns claiming, retries, lease timeouts, terminal failure handling, and finalization.

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
