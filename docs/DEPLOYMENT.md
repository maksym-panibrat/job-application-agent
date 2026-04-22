# Deployment Reference

## 1. Neon Postgres

1. Sign up at <https://neon.tech> and create a project named `job-application-agent` in region `us-east-1`.
2. Copy the **pooled** connection string from the dashboard.
3. Replace the scheme: `postgresql://` → `postgresql+asyncpg://`
4. Save as `DATABASE_URL`.

## 2. Google Cloud setup

Install the gcloud CLI: <https://cloud.google.com/sdk/docs/install>

```bash
gcloud auth login
gcloud projects create job-application-agent-493810
gcloud config set project job-application-agent-493810
gcloud billing accounts list
gcloud billing projects link job-application-agent-493810 --billing-account=BILLING_ACCOUNT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com cloudbuild.googleapis.com
gcloud artifacts repositories create app --repository-format=docker --location=us-central1
```

## 3. Secrets in Secret Manager

### 3a. Required (deploy fails without these)

```bash
printf "%s" "YOUR_GOOGLE_AI_STUDIO_KEY" | gcloud secrets create google-api-key --data-file=-
printf "%s" "YOUR_ADZUNA_APP_ID" | gcloud secrets create adzuna-app-id --data-file=-
printf "%s" "YOUR_ADZUNA_API_KEY" | gcloud secrets create adzuna-api-key --data-file=-
printf "%s" "postgresql+asyncpg://..." | gcloud secrets create database-url --data-file=-
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create cron-shared-secret --data-file=-
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create jwt-secret --data-file=-
```

Verify the cron secret (copy value for GitHub secrets):

```bash
gcloud secrets versions access latest --secret=cron-shared-secret
```

### 3b. Optional (deploy succeeds without; features stay off until created)

The deploy workflow probes these with `gcloud secrets describe` and only includes them in `--set-secrets` if present. Create whichever you want enabled.

**Google OAuth** — required for real Google sign-in. Without these, the app runs with `AUTH_ENABLED=false` (single-user mode, not suitable for multi-user prod).

1. Go to <https://console.cloud.google.com/apis/credentials> (same project). Configure the OAuth consent screen (one-time: User Type = External, app name = Job Application Agent, scopes = `email openid profile`, test users = your email).
2. Create credentials → OAuth client ID → Web application.
3. Authorized redirect URI (after first deploy, replace with your actual Cloud Run URL):
   ```
   https://api-<hash>-uc.a.run.app/auth/google/callback
   ```
4. Copy the Client ID and Client secret, then:
   ```bash
   printf "%s" "PASTE_CLIENT_ID_HERE" | \
     gcloud secrets create google-oauth-client-id --replication-policy=automatic --data-file=-
   printf "%s" "PASTE_CLIENT_SECRET_HERE" | \
     gcloud secrets create google-oauth-client-secret --replication-policy=automatic --data-file=-
   ```

**Sentry DSN** — error/exception tracking SaaS (<https://sentry.io>). Without this the app relies on Cloud Run logs only. See "Observability without Sentry" below if you don't want Sentry.

1. Sign up at sentry.io, create a project (Platform = Python / FastAPI).
2. Copy the DSN from **Settings → Client Keys (DSN)** (format: `https://<pubkey>@o<orgId>.ingest.us.sentry.io/<projectId>`).
3. Store as a GCP secret:
   ```bash
   printf "%s" "PASTE_SENTRY_DSN_HERE" | \
     gcloud secrets create sentry-dsn --replication-policy=automatic --data-file=-
   ```

### 3c. Grant the deploy service account read access

Project-level `roles/secretmanager.secretAccessor` (granted in step 5) covers new secrets automatically. If you see `Permission denied` at deploy time on a specific secret, bind per-secret:

```bash
SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"
for s in google-oauth-client-id google-oauth-client-secret sentry-dsn; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:$SA" \
    --role="roles/secretmanager.secretAccessor"
done
```

## 4. GitHub repo secrets

Add these in **Settings → Secrets and variables → Actions** (or via `gh secret set <name>`):

| Secret | Required? | Value |
|---|---|---|
| `GCP_PROJECT_ID` | yes | `job-application-agent-493810` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | yes | Output from step 5 below |
| `GCP_SERVICE_ACCOUNT` | yes | `github-deployer@job-application-agent-493810.iam.gserviceaccount.com` |
| `CRON_SHARED_SECRET` | yes | `gcloud secrets versions access latest --secret=cron-shared-secret` |
| `CLOUD_RUN_URL` | yes (after step 7) | Cloud Run service URL from step 7 |
| `SMOKE_BEARER_TOKEN` | only if smoke-prod CI enabled | See step 8 |

## 5. Workload Identity Federation (no JSON key)

```bash
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"

SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"
PROJECT="job-application-agent-493810"
REPO="maksym-panibrat/job-application-agent"

for role in roles/run.admin roles/artifactregistry.writer roles/secretmanager.secretAccessor roles/iam.serviceAccountUser roles/cloudbuild.builds.editor roles/storage.admin roles/logging.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA" --role="$role"
done

gcloud iam workload-identity-pools create github-pool --location=global --display-name="GitHub Actions"
POOL_ID=$(gcloud iam workload-identity-pools describe github-pool --location=global --format="value(name)")

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"

gcloud iam service-accounts add-iam-policy-binding $SA \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO}"
```

Get the provider resource name for `GCP_WORKLOAD_IDENTITY_PROVIDER`:

```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global --workload-identity-pool=github-pool --format="value(name)"
```

## 6. Google AI Studio API key

Visit <https://aistudio.google.com/apikey>, generate a key, and store it as the `google-api-key` secret (step 3).

## 7. After first deploy

Get the Cloud Run service URL and add it to GitHub secrets as `CLOUD_RUN_URL`:

```bash
gcloud run services describe api --region us-central1 --format="value(status.url)"
```

Create and run the demo seed job (only needed once):

```bash
IMAGE="us-central1-docker.pkg.dev/job-application-agent-493810/app/api:latest"
SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"

gcloud run jobs create seed-demo \
  --image "$IMAGE" \
  --region us-central1 \
  --command="/app/.venv/bin/python,scripts/seed_demo_profile.py" \
  --set-secrets="DATABASE_URL=database-url:latest" \
  --set-env-vars="PYTHONPATH=/app" \
  --service-account="$SA"

gcloud run jobs execute seed-demo --region us-central1 --wait
```

**Note:** `PYTHONPATH=/app` makes the `app` package importable. The full path to the venv Python is required because Cloud Run's default `PATH` doesn't include the venv.

## 8. Smoke-prod CI wiring (optional)

The `smoke-prod` GitHub Actions job (`.github/workflows/ci.yml`) runs `scripts/smoke/golden_path.py` against the deployed Cloud Run URL after every deploy. If you want this to work, three one-time setup steps:

### 8a. Seed the smoke user in the prod DB

```bash
cd <repo>
DATABASE_URL=$(gcloud secrets versions access latest --secret=database-url) \
  uv run python scripts/seed_smoke_user.py
```

Idempotent — safe to re-run. Creates `smoke@panibrat.com` with UUID `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` and a minimal profile the generation agent can consume.

### 8b. Generate a long-lived JWT and add it to GitHub Actions

```bash
cd <repo>
make smoke-token | tail -1 | gh secret set SMOKE_BEARER_TOKEN --repo <owner>/<repo>
gh secret list --repo <owner>/<repo> | grep SMOKE_BEARER_TOKEN   # verify
```

The token is signed with `JWT_SECRET` (from Secret Manager) and valid for 30 days. Rotate on a calendar reminder, or extend the lifetime in `scripts/make_smoke_token.py` if 30 days is too short.

### 8c. Validate

After next push to main, watch the `smoke-prod` job run. Expected outcome: all 10 steps pass (steps 8a–8d and step 9 are XFAIL placeholders for upstream PRs).

## 9. Observability without Sentry

If you skip step 3b's Sentry DSN, structured logs from the FastAPI app go to **Cloud Run logs only**. Diagnose a cron failure like this:

```bash
# Tail recent app logs
gcloud run services logs read api --region=us-central1 --limit=200

# Zero in on a specific cron job
gcloud run services logs read api --region=us-central1 --limit=500 \
  | grep -E "cron\.(sync|generation_queue|maintenance)\.(failed|budget_exhausted|completed)"
```

Structured events the un-silenced cron handler emits (`app/api/internal_cron.py`):
- `cron.<name>.started` — every invocation
- `cron.<name>.completed` — success (plus the task's result fields)
- `cron.<name>.budget_exhausted` — Gemini quota hit; warning
- `cron.<name>.failed` — unhandled exception; error with `exc_info=True` (full stack trace)

**Alternative**: enable GCP Cloud Error Reporting (free, native) — errors with stack traces get auto-grouped in the GCP console. Enable the API once:

```bash
gcloud services enable clouderrorreporting.googleapis.com
```

Cloud Run stdout logs are already sampled by Error Reporting when they contain `severity=ERROR` (the `_add_cloud_run_severity` structlog processor in `app/main.py:25` already writes that field).

## Troubleshooting

### `forbidden from accessing the bucket [*_cloudbuild]` during deploy

Cloud Build needs `roles/storage.admin` on the service account (included in step 5 above). If you provisioned before this was documented, add it manually:

```bash
gcloud projects add-iam-policy-binding job-application-agent-493810 \
  --member="serviceAccount:github-deployer@job-application-agent-493810.iam.gserviceaccount.com" \
  --role="roles/storage.admin"
```

### `This tool can only stream logs if you are Viewer/Owner` during deploy

`gcloud builds submit` needs `roles/logging.viewer` to stream build logs. If you provisioned before this was documented, add it manually:

```bash
gcloud projects add-iam-policy-binding job-application-agent-493810 \
  --member="serviceAccount:github-deployer@job-application-agent-493810.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"
```

After granting any missing role, re-run the failed GitHub Actions deploy job:

```bash
gh run rerun <run-id> --failed --repo maksym-panibrat/job-application-agent
```
