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

## 4. GitHub repo secrets

Add these in **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `GCP_PROJECT_ID` | `job-application-agent-493810` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Output from step 5 below |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@job-application-agent-493810.iam.gserviceaccount.com` |
| `CRON_SHARED_SECRET` | Output of `gcloud secrets versions access latest --secret=cron-shared-secret` |
| `CLOUD_RUN_URL` | Add after first deploy (step 7) |

## 5. Workload Identity Federation (no JSON key)

```bash
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"

SA="github-deployer@job-application-agent-493810.iam.gserviceaccount.com"
PROJECT="job-application-agent-493810"
REPO="maksym-panibrat/job-application-agent"

for role in roles/run.admin roles/artifactregistry.writer roles/secretmanager.secretAccessor roles/iam.serviceAccountUser roles/cloudbuild.builds.editor roles/storage.admin; do
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

Run the demo seed job:

```bash
gcloud run jobs execute seed-demo --region us-central1 --wait
```

## Troubleshooting

### `forbidden from accessing the bucket [*_cloudbuild]` during deploy

Cloud Build needs `roles/storage.admin` on the service account (included in step 5 above). If you provisioned before this was documented, add it manually:

```bash
gcloud projects add-iam-policy-binding job-application-agent-493810 \
  --member="serviceAccount:github-deployer@job-application-agent-493810.iam.gserviceaccount.com" \
  --role="roles/storage.admin"
```

Then re-run the failed GitHub Actions deploy job:

```bash
gh run rerun <run-id> --failed --repo maksym-panibrat/job-application-agent
```
