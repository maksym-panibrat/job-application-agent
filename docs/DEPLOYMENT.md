# Deployment Reference

Production runtime is owned jointly by this app repo and the `panibrat-infra` repo.

This repo owns application code, tests, migrations, and the Docker image. The infra repo owns the host runtime: compose services, Caddy, cron wiring, release migration execution, rollback, secrets, and observability.

## Why deployment is split this way

- The app repo can stay focused on product behavior and schema changes.
- The infra repo can make host-level changes without mixing them into application PRs.
- Deploy and rollback procedures stay next to the compose files and secrets model they operate on.
- Production verification can use the real host, proxy, scheduler, and logging context.

## Normal release shape

At a high level:

1. This repo builds and publishes an application image from `main`.
2. The infra repo updates the image tag used by production.
3. The infra deploy runs migrations through the guarded release path, starts the app services, checks health, and restores scheduled work.

Exact workflow names, compose service names, host paths, and rollback commands are intentionally not duplicated here. Use `panibrat-infra/docs/runbooks/` and the workflow files in both repos as the current source of truth.

## Application guarantees expected by infra

- The API exposes a cheap health check.
- Long-running work is handled by the worker, not inline web requests.
- Cron endpoints enqueue work only; they do not perform the full job synchronously.
- Migrations can run before the new API/worker image starts.
- Runtime secrets are read from environment variables defined in `app/config.py`.

## Local checks before merge

Run the relevant backend and frontend tests for the code touched. Common checks are documented in the README; exact test coverage should come from the changed code and CI, not from a stale checklist here.
