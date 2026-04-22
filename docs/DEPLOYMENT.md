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
# Gemini key — generate at https://aistudio.google.com/apikey
printf "%s" "YOUR_GOOGLE_AI_STUDIO_KEY" | gcloud secrets create google-api-key --data-file=-
# Adzuna job-search API
printf "%s" "YOUR_ADZUNA_APP_ID" | gcloud secrets create adzuna-app-id --data-file=-
printf "%s" "YOUR_ADZUNA_API_KEY" | gcloud secrets create adzuna-api-key --data-file=-
# Neon Postgres pooled URL (see §1)
printf "%s" "postgresql+asyncpg://..." | gcloud secrets create database-url --data-file=-
# 32-byte randoms for the cron header and JWT signing key
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create cron-shared-secret --data-file=-
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create jwt-secret --data-file=-
```

Fetch the cron secret value — it also goes into GitHub Actions as `CRON_SHARED_SECRET` (§4):

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

> Error monitoring is handled natively via **GCP Cloud Error Reporting** (§8). No third-party SaaS DSN required — the API was enabled in §2.

### 3c. Grant the deploy service account read access

Project-level `roles/secretmanager.secretAccessor` (granted in §5) covers new secrets automatically. If a deploy still fails with `Permission denied` on a specific secret, bind per-secret:

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
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | yes | Output from §5 |
| `GCP_SERVICE_ACCOUNT` | yes | `github-deployer@job-application-agent-493810.iam.gserviceaccount.com` |
| `CRON_SHARED_SECRET` | yes | `gcloud secrets versions access latest --secret=cron-shared-secret` |
| `CLOUD_RUN_URL` | after first deploy | Cloud Run service URL (§6) |
| `SMOKE_BEARER_TOKEN` | smoke-prod CI only | Generated JWT (§7b) |

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

## 6. After first deploy

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

## 7. Smoke-prod CI wiring (optional)

The `smoke-prod` GitHub Actions job (`.github/workflows/ci.yml`) runs `scripts/smoke/golden_path.py` against the deployed Cloud Run URL after every deploy. Two one-time setup steps, then a validation:

### 7a. Seed the smoke user in the prod DB

```bash
cd <repo>
DATABASE_URL=$(gcloud secrets versions access latest --secret=database-url) \
  uv run python scripts/seed_smoke_user.py
```

Idempotent — safe to re-run. Creates `smoke@panibrat.com` with UUID `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` and a minimal profile the generation agent can consume.

### 7b. Generate a long-lived JWT and add it to GitHub Actions

```bash
cd <repo>
make smoke-token | tail -1 | gh secret set SMOKE_BEARER_TOKEN --repo <owner>/<repo>
gh secret list --repo <owner>/<repo> | grep SMOKE_BEARER_TOKEN   # verify
```

The token is signed with `JWT_SECRET` (from Secret Manager) and valid for 30 days. Rotate on a calendar reminder, or extend the lifetime in `scripts/make_smoke_token.py` if 30 days is too short.

### 7c. Validate

Push to main and watch the `smoke-prod` job run. Expected outcome: all steps pass except the generation-flow sub-steps (`8a`–`8d`) and the submit step (`9`), which are XFAIL until the backend flows they test are fixed.

## 8. Observability via GCP Cloud Error Reporting

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

## 9. Manual actions checklist

CI can build images, run Alembic, and roll Cloud Run revisions. Everything below happens outside that loop.

### First-time provisioning (in order)

1. Neon project + `DATABASE_URL` — §1
2. `gcloud projects create` + billing + `gcloud services enable ...` — §2
3. Required secrets in Secret Manager — §3a
4. Service account + Workload Identity Federation pool/provider — §5
5. Repo secrets in GitHub (`GCP_*`, `CRON_SHARED_SECRET`) — §4
6. Push to `main` — first CI run builds image, runs Alembic, deploys
7. Add `CLOUD_RUN_URL` GitHub secret from the deployed service URL — §6
8. (Optional) run the demo seed Cloud Run Job — §6

### Optional features

| Feature | Setup | Reference |
|---|---|---|
| Google sign-in (vs single-user mode) | Create OAuth client + store `google-oauth-client-{id,secret}` in Secret Manager; register the Cloud Run callback URL as an Authorized redirect URI | §3b |
| smoke-prod CI assertions against prod | Seed smoke user in prod DB; generate `SMOKE_BEARER_TOKEN` and set in GHA secrets | §7a, §7b |
| Email / webhook alerts on errors | Error Reporting → Notifications (or Cloud Logging sink → Pub/Sub for Slack/PagerDuty) | §8 |

### Ongoing maintenance

| Cadence | Action | Notes |
|---|---|---|
| Every 30 days | Rotate `SMOKE_BEARER_TOKEN` (JWTs expire) | `make smoke-token \| tail -1 \| gh secret set SMOKE_BEARER_TOKEN` |
| Quarterly | Rotate `cron-shared-secret`, then mirror to `CRON_SHARED_SECRET` in GHA | `openssl rand -hex 32 \| gcloud secrets versions add cron-shared-secret --data-file=-` |
| Quarterly | Rotate `jwt-secret` | **Invalidates every active session.** SMOKE_BEARER_TOKEN must be regenerated afterward |
| On Cloud Run URL change | Update `CLOUD_RUN_URL` (§4) and any OAuth redirect URIs (§3b) | Cloud Run URLs are stable across deploys; this only changes if the service name or region changes |

### Post-provisioning verification

Run this after initial setup to confirm everything is wired:

```bash
URL=$(gcloud run services describe api --region=us-central1 --format='value(status.url)')
SECRET=$(gcloud secrets versions access latest --secret=cron-shared-secret)

# Latest main CI run is green
gh run list --workflow=ci.yml --branch=main --limit=1 --json conclusion,status

# /health responds
curl -s "$URL/health"
# → {"status":"ok","environment":"production",...}

# Cron endpoint accepts the shared secret
curl -s -X POST -H "X-Cron-Secret: $SECRET" "$URL/internal/cron/maintenance"
# → {"status":"ok","duration_ms":...,...}
```

After the first real 500 occurs, it should appear in <https://console.cloud.google.com/errors> within ~1 minute.

## Troubleshooting

If the deploy service account is missing an IAM role added in §5 (common when provisioned before a role was added to the doc):

```bash
PROJECT="job-application-agent-493810"
SA="github-deployer@${PROJECT}.iam.gserviceaccount.com"
# Replace <role> with the missing one from the error, e.g. roles/storage.admin
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" --role="<role>"
```

Common offenders surfaced by Cloud Run / Cloud Build errors:

| Error fragment | Missing role |
|---|---|
| `forbidden from accessing the bucket [*_cloudbuild]` | `roles/storage.admin` |
| `can only stream logs if you are Viewer/Owner` | `roles/logging.viewer` |
| `Secret projects/.../secrets/<name>/versions/latest was not found` | Secret doesn't exist yet (§3) or SA lacks `roles/secretmanager.secretAccessor` on it (§3c) |

Re-run the failed CI after granting the role:

```bash
gh run rerun <run-id> --failed --repo maksym-panibrat/job-application-agent
```
