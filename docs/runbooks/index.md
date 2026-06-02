# Runbooks

Operational procedures that are too specific for the README.

Keep runbooks narrow and actionable. Prefer preconditions, the reason the procedure exists, and where to find the current command implementation. Avoid copying long-lived app contracts from code unless the runbook would be unsafe without them.

- [Production data repair](production-data-repair.md)
- [Batch match canary](batch-match-canary.md)
- [Database query diagnostics](database-diagnostics.md)

Production deploy, rollback, cron, host secrets, Caddy, and Axiom procedures live in the `panibrat-infra` repo because that repo owns the host runtime.
