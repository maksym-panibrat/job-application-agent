# Deployment Reference

## 1. Neon Postgres

Create a project at <https://neon.tech>, grab the **pooled** connection string, replace `postgresql://` with `postgresql+asyncpg://`, save as `DATABASE_URL`.

## 2. Google Cloud setup

Install gcloud: <https://cloud.google.com/sdk/docs/install>

```bash
gcloud auth login
gcloud projects create job-application-agent-493810
gcloud config set project job-application-agent-493810
gcloud billing projects link job-application-agent-493810 --billing-account=BILLING_ACCOUNT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com cloudbuild.googleapis.com clouderrorreporting.googleapis.com
gcloud artifacts repositories create app --repository-format=docker --location=us-central1
```

## 3. Secrets in Secret Manager

### 3a. Required

```bash
printf "%s" "GEMINI_KEY"          | gcloud secrets create google-api-key --data-file=-         # https://aistudio.google.com/apikey
printf "%s" "ADZUNA_APP_ID"       | gcloud secrets create adzuna-app-id --data-file=-
printf "%s" "ADZUNA_API_KEY"      | gcloud secrets create adzuna-api-key --data-file=-
printf "%s" "postgresql+asyncpg://..."      | gcloud secrets create database-url --data-file=-
printf "%s" "$(openssl rand -hex 32)"       | gcloud secrets create cron-shared-secret --data-file=-
printf "%s" "$(openssl rand -hex 32)"       | gcloud secrets create jwt-secret --data-file=-
```

The `cron-shared-secret` value also goes into GHA as `CRON_SHARED_SECRET` (§4):

```bash
gcloud secrets versions access latest --secret=cron-shared-secret
```

### 3b. Optional

Deploy probes these with `gcloud secrets describe` and includes only what exists.

**Google OAuth** — required for real Google sign-in; without it, `AUTH_ENABLED=false` (single-user mode).

1. <https://console.cloud.google.com/apis/credentials> → OAuth consent screen: External, scopes `email openid profile`, add yourself as a test user.
2. Create credentials → OAuth client ID → Web application.
3. Authorized redirect URI: `https://<cloud-run-url>/auth/google/callback`.
4. Store the client id + secret:
   ```bash
   printf "%s" "CLIENT_ID"     | gcloud secrets create google-oauth-client-id --data-file=-
   printf "%s" "CLIENT_SECRET" | gcloud secrets create google-oauth-client-secret --data-file=-
   ```

> Error monitoring uses **GCP Cloud Error Reporting** (§8). No Sentry DSN needed.

### 3c. Per-secret IAM (rare)

Project-level `secretAccessor` (§5) covers new secrets. If a deploy fails with `Permission denied` on a specific one:

```bash
SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"
for s in google-oauth-client-id google-oauth-client-secret; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
done
```

## 4. GitHub repo secrets

**Settings → Secrets and variables → Actions** (or `gh secret set <name>`):

| Secret | When | Value |
|---|---|---|
| `GCP_PROJECT_ID` | required | `job-application-agent-493810` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | required | Output of §5 |
| `GCP_SERVICE_ACCOUNT` | required | `github-deployer@job-application-agent-493810.iam.gserviceaccount.com` |
| `CRON_SHARED_SECRET` | required | `gcloud secrets versions access latest --secret=cron-shared-secret` |
| `CLOUD_RUN_URL` | after §6 | Cloud Run service URL |
| `SMOKE_BEARER_TOKEN` | smoke-prod only | §7b |

## 5. Workload Identity Federation

```bash
PROJECT=job-application-agent-493810
SA=github-deployer@${PROJECT}.iam.gserviceaccount.com
REPO=maksym-panibrat/job-application-agent

gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"

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

# Copy the provider resource name into GHA as GCP_WORKLOAD_IDENTITY_PROVIDER
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global --workload-identity-pool=github-pool --format="value(name)"
```

## 6. After first deploy

Capture the service URL for `CLOUD_RUN_URL`:

```bash
gcloud run services describe api --region us-central1 --format="value(status.url)"
```

Optional demo-data seed (one-time):

```bash
IMAGE=us-central1-docker.pkg.dev/job-application-agent-493810/app/api:latest
SA=github-deployer@job-application-agent-493810.iam.gserviceaccount.com

gcloud run jobs create seed-demo --image "$IMAGE" --region us-central1 \
  --command="/app/.venv/bin/python,scripts/seed_demo_profile.py" \
  --set-secrets="DATABASE_URL=database-url:latest" \
  --set-env-vars="PYTHONPATH=/app" \
  --service-account="$SA"
gcloud run jobs execute seed-demo --region us-central1 --wait
```

`PYTHONPATH=/app` and the full venv path are required because Cloud Run's default `PATH` doesn't include the venv.

## 7. Smoke-prod CI (optional)

`smoke-prod` in `ci.yml` runs `scripts/smoke/golden_path.py` against prod after every deploy.

### 7a. Seed the smoke user

```bash
DATABASE_URL=$(gcloud secrets versions access latest --secret=database-url) \
  uv run python scripts/seed_smoke_user.py
```

Idempotent. Creates `smoke@panibrat.com` (UUID `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee`).

### 7b. Generate + register the JWT

```bash
make smoke-token | tail -1 | gh secret set SMOKE_BEARER_TOKEN --repo <owner>/<repo>
```

Signed with `JWT_SECRET`, valid 30 days.

### 7c. Validate

Push to main. Smoke steps 8a–8d and 9 are XFAIL until the backend flows they test land.

## 8. Observability — GCP Cloud Error Reporting

Errors auto-group at <https://console.cloud.google.com/errors> because `app/main.py::_add_cloud_run_severity` writes `severity=ERROR` and the `@type: …ReportedErrorEvent` marker, and `structlog.processors.format_exc_info` turns `exc_info=True` into a readable traceback under `exception`.

```bash
# Recent errors
gcloud logging read 'severity>=ERROR AND resource.type="cloud_run_revision"' --limit=20 --format=json \
  | jq '.[] | {time: .timestamp, event: .jsonPayload.event, error: .jsonPayload.error, trace: .jsonPayload.exception}'

# A specific cron failure
gcloud logging read 'resource.type="cloud_run_revision" AND jsonPayload.event="cron.generation_queue.failed"' --limit=5 --format=json | jq '.[].jsonPayload'

# Tail the app
gcloud run services logs read api --region=us-central1 --limit=200
```

Cron events (`app/api/internal_cron.py::_run_cron`, each tagged `cron_job=<name>`):

- `cron.<name>.started` / `.completed` — INFO
- `cron.<name>.budget_exhausted` — WARNING, 200 response
- `cron.<name>.failed` — ERROR with stack trace, 500 response, ingested by Error Reporting

Alerts: Error Reporting UI → **Notifications** for email/webhook. For Slack/PagerDuty, route a Cloud Logging sink through Pub/Sub.

## 9. Manual actions

### First-time (in order)

1. Neon + `DATABASE_URL` — §1
2. `gcloud services enable ...` — §2
3. Required secrets — §3a
4. SA + Workload Identity — §5
5. `GCP_*` + `CRON_SHARED_SECRET` in GHA — §4
6. Push to main (first deploy)
7. `CLOUD_RUN_URL` in GHA — §6
8. Demo seed (optional) — §6

### Optional features

| Feature | Setup | Ref |
|---|---|---|
| Google sign-in | OAuth client + `google-oauth-client-{id,secret}` secrets + redirect URI | §3b |
| smoke-prod CI | Seed smoke user + `SMOKE_BEARER_TOKEN` | §7a, §7b |
| Error alerts | Error Reporting → Notifications | §8 |

### Maintenance

| Cadence | Action |
|---|---|
| 30 days | Rotate `SMOKE_BEARER_TOKEN`: `make smoke-token \| tail -1 \| gh secret set SMOKE_BEARER_TOKEN` |
| Quarterly | Rotate `cron-shared-secret`; mirror to GHA |
| Quarterly | Rotate `jwt-secret` — **invalidates every active session**; regenerate smoke token after |
| On URL change | Update `CLOUD_RUN_URL` (§4) and OAuth redirects (§3b) |

### Verification

```bash
URL=$(gcloud run services describe api --region=us-central1 --format='value(status.url)')
SECRET=$(gcloud secrets versions access latest --secret=cron-shared-secret)

gh run list --workflow=ci.yml --branch=main --limit=1 --json conclusion,status
curl -s "$URL/health"                                                       # {"status":"ok",...}
curl -s -X POST -H "X-Cron-Secret: $SECRET" "$URL/internal/cron/maintenance"  # {"status":"ok",...}
```

First real 500 shows up at <https://console.cloud.google.com/errors> within ~1 min.

## Troubleshooting

Missing IAM role for the deploy SA:

```bash
gcloud projects add-iam-policy-binding job-application-agent-493810 \
  --member="serviceAccount:github-deployer@job-application-agent-493810.iam.gserviceaccount.com" \
  --role="<role>"
```

| Error fragment | Missing role |
|---|---|
| `forbidden from accessing the bucket [*_cloudbuild]` | `roles/storage.admin` |
| `can only stream logs if you are Viewer/Owner` | `roles/logging.viewer` |
| `Secret projects/.../secrets/<name>/versions/latest was not found` | Secret doesn't exist (§3) or SA lacks `roles/secretmanager.secretAccessor` on it (§3c) |

Then `gh run rerun <run-id> --failed`.
