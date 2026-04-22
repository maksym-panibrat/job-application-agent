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
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com cloudbuild.googleapis.com clouderrorreporting.googleapis.com
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

> Error monitoring is handled natively via **GCP Cloud Error Reporting** (see §9). No third-party SaaS DSN required — the API was enabled in §2.

### 3c. Grant the deploy service account read access

Project-level `roles/secretmanager.secretAccessor` (granted in step 5) covers new secrets automatically. If you see `Permission denied` at deploy time on a specific secret, bind per-secret:

```bash
SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"
for s in google-oauth-client-id google-oauth-client-secret; do
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

## 9. Observability via GCP Cloud Error Reporting

The app emits structured JSON logs to Cloud Run stdout. Errors get auto-grouped in **GCP Console → Error Reporting** because:

- `app/main.py::_add_cloud_run_severity` tags every log record with `severity` (uppercase) — Cloud Run reads this for its severity badge, and Error Reporting uses it to filter candidate events.
- On `ERROR` / `CRITICAL` records, the same processor also writes `@type: type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent` — the marker that tells Error Reporting to ingest the entry as a first-class error event.
- `structlog.processors.format_exc_info` (also in `configure_logging`) converts `exc_info=True` from `log.aexception` / `log.aerror(..., exc_info=True)` into a readable Python traceback string under `exception`.

### Viewing errors

**Console:** <https://console.cloud.google.com/errors> → select your project. Grouped by exception type + file + line. Each group shows: first-seen, last-seen, event count, affected resources, stack trace, and a link to the source log entry.

**CLI — recent errors:**

```bash
gcloud logging read 'severity>=ERROR AND resource.type="cloud_run_revision"' \
  --limit=20 --format=json | jq '.[] | {time: .timestamp, event: .jsonPayload.event, error: .jsonPayload.error, trace: .jsonPayload.exception}'
```

**CLI — a specific cron failure:**

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND jsonPayload.event="cron.generation_queue.failed"' \
  --limit=5 --format=json | jq '.[] | .jsonPayload'
```

**CLI — tail the app:**

```bash
gcloud run services logs read api --region=us-central1 --limit=200
```

### Event catalogue (what the cron handler emits)

Structured events from `app/api/internal_cron.py::_run_cron` (each record also carries `cron_job=<name>` for filtering):

- `cron.<name>.started` — every invocation (INFO)
- `cron.<name>.completed` — success plus the task's result fields (INFO)
- `cron.<name>.budget_exhausted` — Gemini monthly quota hit; deliberate 200 response (WARNING)
- `cron.<name>.failed` — unhandled exception; 500 response with full `exception` stack trace (ERROR → routes to Error Reporting)

### Alerting (optional)

Error Reporting can email or webhook you on new error groups or spike thresholds. Configure per-project in the Error Reporting UI → **Notifications**. For Slack/PagerDuty integration, use a Cloud Logging sink to Pub/Sub and wire a webhook from there.

## 10. Manual actions checklist

Not everything is automated — the CI pipeline can provision Cloud Run revisions and run Alembic, but these steps happen outside that loop and are easy to miss.

### One-time, before first deploy

| # | Action | Reference |
|---|---|---|
| 1 | Create the Neon project and capture `DATABASE_URL` | §1 |
| 2 | `gcloud projects create` + billing + `gcloud services enable` (includes `clouderrorreporting.googleapis.com`) | §2 |
| 3 | Create required secrets in Secret Manager: `google-api-key`, `adzuna-app-id`, `adzuna-api-key`, `database-url`, `cron-shared-secret`, `jwt-secret` | §3a |
| 4 | Create the `github-deployer` service account + Workload Identity Federation pool/provider | §5 |
| 5 | Add repo secrets in GitHub (`GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `CRON_SHARED_SECRET`) | §4 |
| 6 | Grab a Google AI Studio API key and populate `google-api-key` | §6 |
| 7 | Push to `main` — first CI run builds image, applies Alembic migrations, deploys to Cloud Run | — |
| 8 | Capture Cloud Run URL and add as `CLOUD_RUN_URL` GitHub secret | §7 |
| 9 | Run the demo seed job once if you want fixtures in the DB | §7 |

### Optional, enable as needed

| # | Action | When | Reference |
|---|---|---|---|
| A | Create Google OAuth client (consent screen + Web app credentials); store client id + secret in Secret Manager | When you want real Google sign-in (vs single-user mode) | §3b |
| B | After first deploy, add the Cloud Run URL to the Authorized redirect URIs of the OAuth client (`https://api-<hash>-uc.a.run.app/auth/google/callback`) | Immediately after (A) | §3b |
| C | Seed the smoke user in the prod DB (`scripts/seed_smoke_user.py`) | Before enabling smoke-prod CI | §8a |
| D | Generate `SMOKE_BEARER_TOKEN` with `make smoke-token` and set it as a GitHub Actions secret | Before enabling smoke-prod CI | §8b |
| E | Configure Error Reporting notifications (email or Pub/Sub → Slack/PagerDuty) | When you want alerts instead of polling the UI | §9 |

### Ongoing maintenance

| Cadence | Action | Command / reference |
|---|---|---|
| Every 30 days | Rotate `SMOKE_BEARER_TOKEN` (JWTs expire) | `make smoke-token \| tail -1 \| gh secret set SMOKE_BEARER_TOKEN` |
| Quarterly | Rotate `cron-shared-secret` in Secret Manager + update the `CRON_SHARED_SECRET` GitHub Actions secret | `openssl rand -hex 32 \| gcloud secrets versions add cron-shared-secret --data-file=-` then mirror to `gh secret set CRON_SHARED_SECRET` |
| Quarterly | Rotate `jwt-secret` (**breaks all existing user sessions**; SMOKE_BEARER_TOKEN must be regenerated after this) | `openssl rand -hex 32 \| gcloud secrets versions add jwt-secret --data-file=-`; then regenerate smoke token per above |
| When Cloud Run URL changes | Update `CLOUD_RUN_URL` GitHub secret and any OAuth Authorized redirect URIs | See §7, §3b |
| When adding a new secret to Secret Manager | Grant the deploy service account `roles/secretmanager.secretAccessor` on it | See §3c |
| On new Gemini monthly quota reset | No action — `BudgetExhausted` warnings stop appearing in Error Reporting automatically | — |

### Verifying everything is wired

After initial provisioning, a clean run of the pipeline should show:

```bash
# 1. Main CI is green
gh run list --workflow=ci.yml --branch=main --limit=1 --json conclusion

# 2. All jobs ran (not skipped)
gh run view --json jobs $(gh run list --workflow=ci.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId')

# 3. Cloud Run URL is reachable
curl -s "$(gcloud run services describe api --region=us-central1 --format='value(status.url)')/health"
# → {"status":"ok","environment":"production",...}

# 4. Cron endpoint accepts the shared secret
curl -s -X POST -H "X-Cron-Secret: $(gcloud secrets versions access latest --secret=cron-shared-secret)" \
  "$(gcloud run services describe api --region=us-central1 --format='value(status.url)')/internal/cron/maintenance"
# → {"status":"ok","duration_ms":...,...}

# 5. An error appears in Error Reporting within ~1 minute of happening
# (check after the first real 500: https://console.cloud.google.com/errors)
```

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
