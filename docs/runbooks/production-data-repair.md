# Production Data Repair

This destructive reset is for pre-launch production data repair only. It wipes users, profiles, resumes, applications, generated documents, jobs, queues, usage state, and LangGraph checkpoints.

It preserves companies and invalid slug evidence, and resets non-invalid slug freshness so the recreated owner profile can fetch fresh jobs.

## Preconditions

1. Confirm the target database is production.
2. Pause workers and cron drainers on the host.
3. Confirm you intend to delete user/application/job data.
4. Keep a backup or snapshot if the data might still matter.

## Run

```bash
export DATABASE_URL=postgresql+asyncpg://...
uv run python scripts/wipe_job_data.py --yes-i-mean-prod
make seed-smoke-user
```

## Restore user state

After the wipe:

1. Sign in again.
2. Recreate the owner profile.
3. Upload resume.
4. Follow target companies.
5. Verify `target_company_ids` is non-empty.
6. Trigger sync.
7. Resume workers and cron.
