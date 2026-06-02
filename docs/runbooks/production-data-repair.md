# Production Data Repair

Use this only when intentionally repairing or resetting production-like application data.

This procedure is destructive. It exists because pre-launch or owner-only production data can sometimes be cheaper to recreate than migrate by hand. Do not use it for normal cleanup, user support, or reversible operations.

## Preconditions

- Confirm the exact target database and environment.
- Pause workers and scheduled enqueuers so deleted state is not recreated mid-repair.
- Decide whether a backup/snapshot is needed before deleting data.
- Read the wipe script before running it; the script is the source of truth for what is deleted or preserved.

## Run

Use `scripts/wipe_job_data.py` through `uv run` with the explicit production-confirmation flag required by the script.

If smoke/seed data is needed afterward, use the current make target or script in the repo rather than copying commands from an old runbook.

## Restore application state

After the wipe, recreate state through the product flows: sign in, create the owner profile, upload a resume, follow companies, trigger sync, and verify the UI before resuming scheduled work.
