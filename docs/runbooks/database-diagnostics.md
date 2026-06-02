# Database Query Diagnostics

Use this when investigating database transfer, hot queries, or polling loops.

Postgres can be small at rest while hot paths repeatedly fetch wide rows. Measure query shape before changing code.

## Reset stats before a measurement window

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT pg_stat_statements_reset();
```

## Collect diagnostics

After at least 24 hours of representative traffic:

```bash
psql "$DATABASE_URL" -f scripts/neon_egress_diagnostics.sql
```

The script name is historical; use it for any Postgres deployment where `pg_stat_statements` is available.

## Interpret

- High `rows` plus wide tables such as `jobs` usually means high transfer.
- High `calls` means polling, cron, or auth loops may dominate even with small rows.
- `pg_stat_statements` does not report exact bytes. Compare rows, calls, and table stats before and after changes.

Expected healthy production cadence:

- `/internal/cron/sync`: about 4/day.
- `/internal/cron/generation-reconcile`: about 48/day.
- `/internal/cron/maintenance`: about 1/day.
- `/api/sync/status`: only while an authenticated user has live sync/match work.
- `/api/status`: cached on the frontend, not a per-component minute loop.
