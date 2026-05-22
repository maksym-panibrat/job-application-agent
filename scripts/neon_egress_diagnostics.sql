-- Neon egress diagnostics.
-- Usage:
--   psql "$DATABASE_URL" -f scripts/neon_egress_diagnostics.sql
--
-- These reports rank query candidates by rows and call frequency. They are not
-- exact byte-level network-transfer reports.

\echo 'pg_stat_statements availability'
SELECT EXISTS (
  SELECT 1
  FROM pg_extension
  WHERE extname = 'pg_stat_statements'
) AS pg_stat_statements_installed;

\echo 'top total returned rows'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY rows DESC
LIMIT 20;

\echo 'top rows per call'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY avg_rows_per_call DESC NULLS LAST
LIMIT 20;

\echo 'most frequent queries'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY calls DESC
LIMIT 20;

\echo 'longest total execution time'
SELECT query, calls, rows AS total_rows,
       round(total_exec_time::numeric, 2) AS total_exec_time_ms
FROM pg_stat_statements
WHERE calls > 0
ORDER BY total_exec_time DESC
LIMIT 20;

\echo 'hot table stats'
SELECT relname,
       n_live_tup,
       n_dead_tup,
       seq_scan,
       seq_tup_read,
       idx_scan,
       idx_tup_fetch,
       n_tup_ins,
       n_tup_upd,
       n_tup_del,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
ORDER BY seq_tup_read + idx_tup_fetch DESC
LIMIT 30;
