# Database Query Diagnostics

Use this when investigating database transfer, hot queries, or polling loops.

Postgres can be small at rest while hot paths repeatedly fetch wide rows. Measure query shape before changing code.

## Why this exists

The app has background sync, worker polling, auth/session checks, and frontend polling for user-visible async work. A small mistake in any of those paths can create noisy database traffic without obvious product symptoms.

## Measurement approach

1. Enable or confirm `pg_stat_statements` for the database being measured.
2. Reset stats at the start of a known measurement window.
3. Let representative traffic run long enough to include cron, worker, and frontend behavior.
4. Run the diagnostics script in `scripts/` against the target database.
5. Compare rows, calls, and table/query shape before and after code changes.

The diagnostics script name may be historical; the script content is the source of truth for what it measures.

## Interpret

- High row counts on wide tables usually point to transfer-heavy queries.
- High call counts usually point to polling, cron cadence, auth/session checks, or worker loops.
- `pg_stat_statements` does not report exact bytes. Treat it as a guide to where to inspect code and logs next.
- Prefer fixing query shape and polling behavior over adding more documentation about expected counts.
