# Deployment and Release Troubleshooting

Production is jointly owned by this application repository and
[`panibrat-infra`](https://github.com/maksym-panibrat/panibrat-infra). This repo
owns application code, migrations, CI, and the Docker image. The infra repo owns
the pinned production image, host Compose services, release migration execution,
Caddy, scheduler, secrets, observability, deploy health checks, and rollback.

## Release path

A normal release crosses these boundaries in order:

1. A push to this repo's `main` starts the **CI** workflow (`.github/workflows/ci.yml`).
2. After backend, frontend, and browser jobs pass, `build-and-push` builds the
   image, verifies its runtime imports, and pushes both the immutable commit tag
   and `:main` to `ghcr.io/maksym-panibrat/job-application-agent`.
3. The same job sends a `repository_dispatch` with event type
   `bump-app-image`, app `job-search`, and the application commit SHA to
   `panibrat-infra`.
4. Infra's **bump** workflow (`.github/workflows/bump.yml`) changes the
   `x-job-search-image` pin in `compose.yml` and opens a
   `deploy(job-search): bump to <sha>` PR. An operator reviews and merges it;
   deployment is deliberately not automatic before that merge.
5. The merge to infra `main` starts infra's **deploy** workflow
   (`.github/workflows/deploy.yml`). It invokes the infra-owned deploy script,
   which pauses scheduled work, ensures Postgres is healthy, runs the release
   migration, replaces API and worker containers, checks health/routing, and
   then attempts to resume scheduling. Not every failure is a rollback: an
   abort before cutover (for example, scheduler-stop or database-readiness
   failure) exits without changing the app image, while migration, worker, API
   health, and route-gate failures enter the current image/crontab rollback
   block. A scheduler restart can also fail after the app has passed acceptance,
   in which case the app is not rolled back. Always inspect the failed run and
   verify scheduler state explicitly.

A manual `workflow_dispatch` of app CI is not build-only. For the selected ref,
`build-and-push` pushes the immutable SHA **and overwrites `:main`**, then sends
the same infra `bump-app-image` dispatch. That can open an infra bump PR for a
feature-branch SHA. Diagnose and review it by the workflow run's actual SHA; do
not assume either the mutable tag or a manual run represents app `main`.

## Trace one release end to end

Set the immutable application SHA you expect to deploy:

```bash
APP_SHA=$(git rev-parse HEAD)
git show --stat --oneline "$APP_SHA"
```

### 1. App CI and image build

```bash
gh run list --repo maksym-panibrat/job-application-agent \
  --workflow ci.yml --commit "$APP_SHA" --limit 10 \
  --json databaseId,headSha,status,conclusion,url
APP_RUN_ID=... # copy the matching databaseId from the list
gh run view "$APP_RUN_ID" --repo maksym-panibrat/job-application-agent
gh run view "$APP_RUN_ID" --repo maksym-panibrat/job-application-agent --log-failed
```

The `build-and-push` job cannot run until all required test jobs pass. If tests
passed but no image exists, inspect its `Login to GHCR`, `Build and push`, and
runtime-import verification output. To verify the immutable tag from a machine
already authorized for this private package:

```bash
docker manifest inspect \
  "ghcr.io/maksym-panibrat/job-application-agent:$APP_SHA" >/dev/null
```

Do not diagnose production from the mutable `:main` tag; infra pins the commit
SHA.

### 2. Dispatch into infra

The dispatch is the final step of app `build-and-push`. If the image exists but
no infra run appears, inspect that step for token permission, repository, event
type, and payload failures:

```bash
gh run view "$APP_RUN_ID" --repo maksym-panibrat/job-application-agent --log

gh run list --repo maksym-panibrat/panibrat-infra \
  --workflow bump.yml --event repository_dispatch --limit 20 \
  --json databaseId,createdAt,status,conclusion,url
BUMP_RUN_ID=...
gh run view "$BUMP_RUN_ID" --repo maksym-panibrat/panibrat-infra --log-failed
```

A failed or missing dispatch does not mean the image build failed; these are
separate boundaries. Never paste dispatch credentials or workflow environment
values into tickets or logs.

### 3. Infra bump PR

```bash
gh pr list --repo maksym-panibrat/panibrat-infra --state all \
  --search "deploy(job-search): bump to $APP_SHA in:title"
INFRA_PR=...
gh pr view "$INFRA_PR" --repo maksym-panibrat/panibrat-infra
gh pr diff "$INFRA_PR" --repo maksym-panibrat/panibrat-infra
```

A normal bump changes only the job-search image pin. If the image declares a
required infra bundle, the bump workflow intentionally opens a blocked draft;
follow the coordinated-deploy section of the infra deploy runbook rather than
merging it directly. If the PR exists but production has not changed, verify
that it was reviewed and merged—opening the PR is not a deployment.

### 4. Infra deploy and migration boundary

```bash
gh run list --repo maksym-panibrat/panibrat-infra \
  --workflow deploy.yml --branch main --limit 20 \
  --json databaseId,headSha,createdAt,status,conclusion,url
DEPLOY_RUN_ID=...
gh run view "$DEPLOY_RUN_ID" --repo maksym-panibrat/panibrat-infra
gh run view "$DEPLOY_RUN_ID" --repo maksym-panibrat/panibrat-infra --log-failed
```

For job-search, the release migration runs after the image is pulled and local
Postgres is healthy, but **before** the new API and worker are started. A failure
or timeout in `alembic-upgrade` is therefore a migration/deploy failure, not an
API-health failure. The current merged infra script routes that failure through
its previous-image/previous-crontab rollback block; this is different from an
earlier database-readiness abort, which occurs before cutover and does not invoke
that block. Inspect migration output, rollback output, and the actual database
revision rather than assuming either succeeded. Do not run an ad-hoc production
migration from an application checkout.

If migration succeeds but container startup or route checks fail, inspect the
same deploy run for API/worker state and rollback output. A rollback attempt is
not proof of rollback success or schema compatibility. Runtime service names,
host commands, timeout behavior, and rollback mechanics are intentionally
maintained in infra, not duplicated here.

### Required checks after any failed deploy

Whether the run says `aborting cutover`, `engaging rollback`, or fails after the
app becomes healthy, verify all of these from the current infra runbook and host
evidence before retrying:

- immutable image actually running for both API and worker;
- Alembic revision/schema compatibility with that image;
- rollback overlay and previous-crontab state, when the rollback block ran;
- `supercronic` container state **and** whether the operator/deploy pause flag is
  present; and
- fresh scheduler execution evidence after scheduling is expected to resume.

Do not infer scheduler recovery from the deploy workflow conclusion. In the
current infra script, some pre-cutover exits happen after scheduler handling,
and scheduler restart failure can happen after app acceptance without triggering
an image rollback.

## Health is not queue completion

The API's `/health` response proves the web process can answer a cheap request.
Infra's deploy gate uses the API container's Docker healthcheck and separately
requires the worker container to be running. Neither condition proves that a
sync, match, batch import, cover-letter generation, or maintenance job finished.

After an apparently healthy deploy, verify asynchronous behavior separately:

- API availability and queue-depth emission;
- a current `worker.started` event;
- queue depth and oldest in-progress age returning to expected levels;
- `worker.job_done` throughput by job type;
- no sustained `worker.job_failed` or terminal-hook failures;
- scheduler completion events when the release affects cron-driven work.

The canonical queries and dataset routing are in
`panibrat-infra/docs/runbooks/observability.md`; deploy mechanics are in
`panibrat-infra/docs/runbooks/deploy.md`; recovery is in
`panibrat-infra/docs/runbooks/rollback.md`; and scheduler checks are in
`panibrat-infra/docs/runbooks/cron.md`. Use those current infra-owned runbooks
for host/runtime diagnosis.

## Catalog validation is a separate signal

The scheduled `validate-catalog` workflow in this repo probes curated public ATS
catalog entries. It does **not** deploy, enqueue production sync, call the
production cron path, or prove that active profiles were synchronized. Diagnose
production catalog freshness through scheduler, API, worker, and queue evidence;
do not treat a green catalog-validation run as production-sync confirmation.

## Before merging application changes

Use the clone doctor and locked installs, then run checks appropriate to the
change:

```bash
make preflight
uv sync --locked --dev
uv run ruff check app/ tests/
uv run pytest tests/unit/
(cd frontend && npm ci && npm test && npm run build)
```

Production migrations are applied only by the infra release path. For local
migration work, use `make migrate ARGS="..."`; its guard refuses write commands
against non-local database hosts unless explicitly overridden.
