# Phase 1 — Make it shippable: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a broken-CI, undeployed repo into a green-CI Cloud Run service running on Gemini with an externalised cron scheduler and a live URL.

**Architecture:** Four parallel workstreams land in sequence: (1) CI green locally before touching infra, (2) scheduler moved out of the web process into GitHub Actions cron hitting `/internal/cron/*` HTTP endpoints, (3) Anthropic → Gemini LLM swap across all four call sites, (4) Dockerfile slimming + dead code removal. Then deploy pipeline is wired in CI and the first Cloud Run deploy runs.

**Tech stack:** FastAPI 0.115, LangGraph 0.2, `langchain-google-genai` (replaces `langchain-anthropic`), Neon Postgres (asyncpg + psycopg v3), Google Cloud Run (free tier, `$PORT` listener), GitHub Actions cron, `uv`, `ruff`, `vitest`, Playwright.

**Spec:** `docs/superpowers/specs/2026-04-19-portfolio-ship-phases-1-2-design.md`

---

## Prerequisites (user runs once, before Task 1)

These are **manual steps**, not code. Complete all before starting Task 1.

### P1 — Neon Postgres

1. Sign up at neon.tech (free tier).
2. Create project `job-application-agent`, region `us-east-1`.
3. Copy the **pooled** connection string (looks like `postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require`).
4. Replace `postgresql://` with `postgresql+asyncpg://` — save this as `DATABASE_URL`.

### P2 — Google Cloud setup

```bash
# Install gcloud CLI if not already: https://cloud.google.com/sdk/docs/install
gcloud auth login
gcloud projects create job-application-agent-portfolio
gcloud config set project job-application-agent-portfolio
gcloud billing accounts list          # find BILLING_ACCOUNT_ID
gcloud billing projects link job-application-agent-portfolio \
  --billing-account=BILLING_ACCOUNT_ID
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  cloudbuild.googleapis.com
gcloud artifacts repositories create app \
  --repository-format=docker \
  --location=us-central1
```

### P3 — Secrets in Secret Manager

```bash
# Replace values with your real keys
printf "%s" "YOUR_GOOGLE_AI_STUDIO_KEY" \
  | gcloud secrets create google-api-key --data-file=-

printf "%s" "YOUR_ADZUNA_APP_ID" \
  | gcloud secrets create adzuna-app-id --data-file=-

printf "%s" "YOUR_ADZUNA_API_KEY" \
  | gcloud secrets create adzuna-api-key --data-file=-

printf "%s" "postgresql+asyncpg://user:pass@ep-xxx.neon.tech/neondb?sslmode=require" \
  | gcloud secrets create database-url --data-file=-

printf "%s" "$(openssl rand -hex 32)" \
  | gcloud secrets create cron-shared-secret --data-file=-

printf "%s" "$(openssl rand -hex 32)" \
  | gcloud secrets create jwt-secret --data-file=-

# Save cron-shared-secret locally — you'll need it for the GitHub secret
gcloud secrets versions access latest --secret=cron-shared-secret
```

### P4 — GitHub repo secrets

In `https://github.com/<you>/job-application-agent/settings/secrets/actions`, add:

| Secret name | Value |
|---|---|
| `GCP_PROJECT_ID` | `job-application-agent-portfolio` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | (from P5 below) |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@job-application-agent-portfolio.iam.gserviceaccount.com` |
| `CRON_SHARED_SECRET` | (output of `gcloud secrets versions access latest --secret=cron-shared-secret`) |

### P5 — Workload Identity Federation (no JSON key)

```bash
# Service account
gcloud iam service-accounts create github-deployer \
  --display-name="GitHub Actions deployer"

SA="github-deployer@job-application-agent-portfolio.iam.gserviceaccount.com"
PROJECT="job-application-agent-portfolio"
REPO="<your-github-username>/job-application-agent"

for role in roles/run.admin roles/artifactregistry.writer \
            roles/secretmanager.secretAccessor \
            roles/iam.serviceAccountUser \
            roles/cloudbuild.builds.editor; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" --role="$role"
done

# Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location=global --display-name="GitHub Actions"

POOL_ID=$(gcloud iam workload-identity-pools describe github-pool \
  --location=global --format="value(name)")

# OIDC provider
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"

# Allow impersonation from this repo
gcloud iam service-accounts add-iam-policy-binding $SA \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO}"

# Print the provider resource name — add to GCP_WORKLOAD_IDENTITY_PROVIDER secret
gcloud iam workload-identity-pools providers describe github-provider \
  --location=global --workload-identity-pool=github-pool \
  --format="value(name)"
```

### P6 — Get a Google AI Studio API key

Visit https://aistudio.google.com/apikey, create a key. Store it somewhere safe — Task 14 will add it to `.env`.

---

## Task 1: Fix the ruff lint failure

**Files:**
- Modify: `app/agents/onboarding.py:201`

- [ ] **1a. Reproduce the failure locally**

```bash
uv run ruff check app/ tests/
```
Expected output: `app/agents/onboarding.py:201:101: E501 Line too long (101 > 100)`

- [ ] **1b. Open `app/agents/onboarding.py` at line 201. Wrap the call to fit within 100 chars.**

Current line (101 chars):
```python
                        await profile_service.upsert_work_experience(profile_uuid, exp_copy, session)
```

Replace with:
```python
                        await profile_service.upsert_work_experience(
                            profile_uuid, exp_copy, session
                        )
```

- [ ] **1c. Verify lint passes**

```bash
uv run ruff check app/ tests/
```
Expected: no output (exit 0).

- [ ] **1d. Commit**

```bash
git add app/agents/onboarding.py
git commit -m "fix: wrap long line in onboarding.py to pass ruff E501"
```

---

## Task 2: Fix Vitest picking up Playwright e2e specs

**Files:**
- Modify: `frontend/vite.config.ts`

- [ ] **2a. Reproduce the failure locally**

```bash
cd frontend && npm test 2>&1 | head -20
```
Expected: error mentioning `test.describe() not expected` from `e2e/onboarding.spec.ts`.

- [ ] **2b. Edit `frontend/vite.config.ts` — add `exclude` to the `test` block**

Current `test` block (lines 6–10):
```ts
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
```

Replace with:
```ts
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
```

- [ ] **2c. Verify Vitest passes**

```bash
cd frontend && npm test
```
Expected: runs only `src/test/MatchCard.test.tsx`, exits green.

- [ ] **2d. Commit**

```bash
git add frontend/vite.config.ts
git commit -m "fix: exclude e2e/ from vitest to prevent @playwright/test import"
```

---

## Task 3: Upgrade stale GitHub Actions versions + fix cross-workflow needs

**Files:**
- Modify: `.github/workflows/ci.yml`
- Delete: `.github/workflows/deploy.yml`

- [ ] **3a. Delete `deploy.yml`** (the cross-workflow `needs:` in it is invalid; deploy will move into `ci.yml` in Task 30)

```bash
git rm .github/workflows/deploy.yml
```

- [ ] **3b. Upgrade action versions in `ci.yml`**

In `.github/workflows/ci.yml`, make these replacements everywhere they appear:

| Old | New |
|---|---|
| `actions/checkout@v4` | `actions/checkout@v5` |
| `actions/setup-node@v4` | `actions/setup-node@v5` |
| `astral-sh/setup-uv@v3` | `astral-sh/setup-uv@v5` |
| `actions/upload-artifact@v4` | `actions/upload-artifact@v4` (already fine, keep) |
| `actions/cache@v4` | `actions/cache@v4` (already fine, keep) |

- [ ] **3c. Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/deploy.yml
git commit -m "fix: upgrade GHA actions to v5, remove broken deploy.yml"
```

---

## Task 4: Externalise scheduler — add cron secret to config

**Files:**
- Modify: `app/config.py`

- [ ] **4a. Open `app/config.py`. Add `cron_shared_secret` field**

Find the `Settings` class. Add these fields (after `jwt_secret` or near the auth section):

```python
cron_shared_secret: SecretStr = SecretStr("dev-cron-secret")
```

Also rename the LLM fields while in this file (they'll be needed in Task 14, but do it now to avoid a conflict later):

```python
# Replace:
anthropic_api_key: SecretStr
claude_model: str = "claude-sonnet-4-6"
claude_matching_model: str = "claude-haiku-4-5-20251001"
anthropic_base_url: str | None = None

# With:
google_api_key: SecretStr = SecretStr("")
llm_generation_model: str = "gemini-2.5-pro"
llm_matching_model: str = "gemini-2.5-flash"
llm_resume_extraction_model: str = "gemini-2.5-flash"
```

Keep all other fields unchanged.

- [ ] **4b. Update `.env.example`** — replace Anthropic vars with Google:

```
DATABASE_URL=postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent
GOOGLE_API_KEY=your-google-ai-studio-key-here
ADZUNA_APP_ID=your-app-id
ADZUNA_API_KEY=your-api-key
ENVIRONMENT=development
AUTH_ENABLED=false
JWT_SECRET=dev-secret-change-in-prod
CRON_SHARED_SECRET=dev-cron-secret
SENTRY_DSN=
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=job-application-agent
LOG_LEVEL=INFO
JSEARCH_API_KEY=
LLM_GENERATION_MODEL=gemini-2.5-pro
LLM_MATCHING_MODEL=gemini-2.5-flash
LLM_RESUME_EXTRACTION_MODEL=gemini-2.5-flash
```

- [ ] **4c. Update your local `.env`** — add `GOOGLE_API_KEY=<your AI Studio key>`, remove `ANTHROPIC_API_KEY`.

- [ ] **4d. Run unit tests to confirm config still loads**

```bash
uv run pytest tests/unit/ -v -k "config or settings" 2>&1 | tail -10
```
Expected: any config-related tests pass. If none exist, that's fine.

- [ ] **4e. Commit**

```bash
git add app/config.py .env.example
git commit -m "feat: add cron_shared_secret and rename LLM config fields for Gemini"
```

---

## Task 5: Write failing tests for cron endpoints

**Files:**
- Create: `tests/unit/test_internal_cron.py`

- [ ] **5a. Create the test file**

```python
# tests/unit/test_internal_cron.py
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings


def make_app(secret: str = "test-secret"):
    from app.api.internal_cron import router, get_cron_settings

    test_app = FastAPI()
    test_app.include_router(router)

    override_settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        cron_shared_secret=secret,
        google_api_key="fake",
    )
    test_app.dependency_overrides[get_cron_settings] = lambda: override_settings
    return TestClient(test_app)


def test_sync_missing_secret_returns_403():
    client = make_app()
    resp = client.post("/internal/cron/sync")
    assert resp.status_code == 403


def test_sync_wrong_secret_returns_403():
    client = make_app(secret="real-secret")
    resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 403


def test_sync_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch("app.api.internal_cron.run_job_sync", new=AsyncMock(return_value=None)) as mock:
        resp = client.post("/internal/cron/sync", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()


def test_generation_queue_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch("app.api.internal_cron.run_generation_queue", new=AsyncMock()) as mock:
        resp = client.post("/internal/cron/generation-queue", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()


def test_maintenance_correct_secret_calls_task():
    client = make_app(secret="real-secret")
    with patch("app.api.internal_cron.run_daily_maintenance", new=AsyncMock()) as mock:
        resp = client.post("/internal/cron/maintenance", headers={"X-Cron-Secret": "real-secret"})
    assert resp.status_code == 200
    mock.assert_called_once()
```

- [ ] **5b. Run — expect failure (module doesn't exist yet)**

```bash
uv run pytest tests/unit/test_internal_cron.py -v 2>&1 | tail -15
```
Expected: `ModuleNotFoundError: No module named 'app.api.internal_cron'`

- [ ] **5c. Commit the test file**

```bash
git add tests/unit/test_internal_cron.py
git commit -m "test: add failing tests for /internal/cron/* endpoints"
```

---

## Task 6: Implement `/internal/cron/*` endpoints

**Files:**
- Create: `app/api/internal_cron.py`

- [ ] **6a. Create the router**

```python
# app/api/internal_cron.py
from fastapi import APIRouter, Depends, Header, HTTPException
from app.config import Settings, get_settings
from app.scheduler.tasks import run_daily_maintenance, run_generation_queue, run_job_sync

router = APIRouter(prefix="/internal/cron", tags=["cron"])


def get_cron_settings() -> Settings:
    return get_settings()


async def verify_secret(
    x_cron_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_cron_settings),
) -> None:
    expected = settings.cron_shared_secret.get_secret_value()
    if x_cron_secret is None or x_cron_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid cron secret")


@router.post("/sync", dependencies=[Depends(verify_secret)])
async def cron_sync():
    await run_job_sync()
    return {"status": "ok"}


@router.post("/generation-queue", dependencies=[Depends(verify_secret)])
async def cron_generation_queue():
    await run_generation_queue()
    return {"status": "ok"}


@router.post("/maintenance", dependencies=[Depends(verify_secret)])
async def cron_maintenance():
    await run_daily_maintenance()
    return {"status": "ok"}
```

- [ ] **6b. Run the tests — expect pass**

```bash
uv run pytest tests/unit/test_internal_cron.py -v
```
Expected: 5 passed.

- [ ] **6c. Commit**

```bash
git add app/api/internal_cron.py
git commit -m "feat: add /internal/cron/* endpoints with shared-secret auth"
```

---

## Task 7: Register the cron router + strip APScheduler from main.py

**Files:**
- Modify: `app/main.py`

- [ ] **7a. Open `app/main.py`. Find the `include_router` block. Add the cron router (unconditionally — it is protected by the shared secret, not by env):**

Find the section where other routers are registered (look for `app.include_router`). Add:

```python
from app.api.internal_cron import router as cron_router
app.include_router(cron_router)
```

- [ ] **7b. Find the lifespan block. Remove the APScheduler section.**

Current lifespan section (around lines 77–85):
```python
    scheduler = None
    if settings.environment == "production":
        from app.scheduler.tasks import setup_scheduler
        scheduler = setup_scheduler(app)
        await log.ainfo("scheduler.started")
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
```

Replace with just:
```python
    yield
```

(Keep all other lifespan logic — Sentry, structlog, init_db, AsyncPostgresSaver — unchanged.)

- [ ] **7c. Verify the app still starts (no import errors)**

```bash
uv run uvicorn app.main:app --port 8001 --log-level warning &
sleep 3
curl -s http://localhost:8001/health
kill %1
```
Expected: health endpoint returns 200 or JSON ok.

- [ ] **7d. Verify the cron endpoint is reachable and requires the secret**

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8001/internal/cron/sync
```
Expected: `403`

- [ ] **7e. Commit**

```bash
git add app/main.py
git commit -m "feat: register cron router, remove APScheduler from lifespan"
```

---

## Task 8: Remove APScheduler + greenlet from pyproject.toml

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/scheduler/tasks.py`

- [ ] **8a. Open `app/scheduler/tasks.py`. Delete the `setup_scheduler` function (lines 108–147 or wherever it ends). Keep `run_job_sync`, `run_generation_queue`, `run_daily_maintenance` unchanged.**

The function to delete starts with:
```python
def setup_scheduler(app: FastAPI) -> AsyncIOScheduler:
```
Delete it and any imports used only by it (typically `from apscheduler.schedulers.asyncio import AsyncIOScheduler`, `from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore`).

- [ ] **8b. Open `pyproject.toml`. Remove these two lines from `dependencies`:**

```toml
"apscheduler>=3.10,<4",
"greenlet>=3.0",
```

- [ ] **8c. Sync the lockfile**

```bash
uv sync --dev
```
Expected: uv removes apscheduler and greenlet from `.venv`, rewrites `uv.lock`.

- [ ] **8d. Run unit tests to confirm nothing broke**

```bash
uv run pytest tests/unit/ -v 2>&1 | tail -20
```
Expected: all pass.

- [ ] **8e. Commit**

```bash
git add pyproject.toml uv.lock app/scheduler/tasks.py
git commit -m "chore: remove APScheduler; cron now triggered via GitHub Actions HTTP"
```

---

## Task 9: Create the GitHub Actions cron workflow

**Files:**
- Create: `.github/workflows/cron.yml`

- [ ] **9a. Create the file**

```yaml
# .github/workflows/cron.yml
name: Cron

on:
  schedule:
    - cron: '0 */4 * * *'    # job sync every 4 hours
    - cron: '*/10 * * * *'   # generation queue every 10 min
    - cron: '0 3 * * *'      # maintenance daily at 03:00 UTC
  workflow_dispatch:           # allow manual trigger for testing

jobs:
  sync:
    if: github.event.schedule == '0 */4 * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger job sync
        run: |
          curl -sf -X POST \
            -H "X-Cron-Secret: ${{ secrets.CRON_SHARED_SECRET }}" \
            https://${{ secrets.CLOUD_RUN_URL }}/internal/cron/sync

  generation-queue:
    if: github.event.schedule == '*/10 * * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger generation queue
        run: |
          curl -sf -X POST \
            -H "X-Cron-Secret: ${{ secrets.CRON_SHARED_SECRET }}" \
            https://${{ secrets.CLOUD_RUN_URL }}/internal/cron/generation-queue

  maintenance:
    if: github.event.schedule == '0 3 * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger daily maintenance
        run: |
          curl -sf -X POST \
            -H "X-Cron-Secret: ${{ secrets.CRON_SHARED_SECRET }}" \
            https://${{ secrets.CLOUD_RUN_URL }}/internal/cron/maintenance
```

Note: `CLOUD_RUN_URL` will be added to GitHub secrets in Task 31 (after first deploy). For now the file can exist without that secret — the jobs won't fire successfully until the URL is set, but that's fine.

- [ ] **9b. Also add `CLOUD_RUN_URL` to the GitHub secrets list** (add it to repo secrets after first deploy, Task 33).

- [ ] **9c. Commit**

```bash
git add .github/workflows/cron.yml
git commit -m "feat: add cron.yml to trigger scheduled tasks via GitHub Actions"
```

---

## Task 10: Update LangGraph / AsyncPostgresSaver — verify idempotent setup

**Files:**
- Read: `app/main.py` (the `AsyncPostgresSaver.setup()` call)

- [ ] **10a. Find the `AsyncPostgresSaver.setup()` call in `app/main.py` lifespan. Verify it uses `IF NOT EXISTS` patterns.**

Look for something like:
```python
async with await AsyncPostgresSaver.from_conn_string(...) as checkpointer:
    await checkpointer.setup()
```

The LangGraph source for `AsyncPostgresSaver.setup()` calls:
```sql
CREATE TABLE IF NOT EXISTS checkpoints ...
CREATE TABLE IF NOT EXISTS checkpoint_blobs ...
CREATE INDEX IF NOT EXISTS ...   -- may not include IF NOT EXISTS
```

- [ ] **10b. If `CREATE INDEX CONCURRENTLY` appears without `IF NOT EXISTS`, wrap the setup call in a try/except to swallow the "already exists" error on cold restarts:**

```python
try:
    await checkpointer.setup()
except Exception as exc:
    if "already exists" not in str(exc).lower():
        raise
```

If the index creation is already idempotent, no change needed.

- [ ] **10c. Commit if changed**

```bash
git add app/main.py
git commit -m "fix: guard AsyncPostgresSaver.setup() against duplicate-index on cold restart"
```

---

## Task 11: Add test for `/api/test/*` 404 in production

**Files:**
- Modify: `tests/unit/test_prod_guard.py` (create if it doesn't exist)

- [ ] **11a. Write the test**

```python
# tests/unit/test_prod_guard.py
import os
import pytest
from fastapi.testclient import TestClient


def test_test_helpers_not_mounted_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")

    # Clear settings singleton so it re-reads env
    import app.config as cfg_module
    cfg_module._settings = None

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/api/test/seed")
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}"

    # Reset for other tests
    cfg_module._settings = None
```

- [ ] **11b. Run — it may already pass (current mount condition is `environment == "development"`)**

```bash
uv run pytest tests/unit/test_prod_guard.py -v
```
Expected: PASS (the existing gate already blocks production). If it fails, the gate logic is broken — investigate `app/main.py` lines 122–125.

- [ ] **11c. Commit**

```bash
git add tests/unit/test_prod_guard.py
git commit -m "test: assert /api/test/* returns 404 when ENVIRONMENT=production"
```

---

## Task 12: Remove dead code

**Files:**
- Delete: `app/schemas/__init__.py` (and the `schemas/` directory)

- [ ] **12a. Verify `app/schemas/` is truly empty**

```bash
find app/schemas/ -type f
```
Expected: only `app/schemas/__init__.py` with 0 bytes.

- [ ] **12b. Remove it**

```bash
git rm -r app/schemas/
```

- [ ] **12c. Grep for any imports from `app.schemas`**

```bash
grep -r "from app.schemas" app/ tests/ --include="*.py"
```
Expected: no results.

- [ ] **12d. Commit**

```bash
git commit -m "chore: remove empty app/schemas/ package"
```

---

## Task 13: Slim the Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **13a. Apply these changes to the runtime stage:**

Current runtime stage (lines 13–25):
```dockerfile
FROM python:3.12-bookworm AS runtime
# WeasyPrint requires Cairo + Pango at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 shared-mime-info fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/ ./app/
COPY --from=frontend-builder /frontend/dist ./app/static/
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Replace with:
```dockerfile
FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/ ./app/
COPY --from=frontend-builder /frontend/dist ./app/static/
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

Changes: slim base (no WeasyPrint system libs), listen on `$PORT` with fallback to 8000 (Cloud Run passes `$PORT` at runtime).

- [ ] **13b. Build the image locally to verify**

```bash
docker build -t job-agent-test . 2>&1 | tail -5
```
Expected: `Successfully built …`

- [ ] **13c. Verify the app starts from the image**

```bash
docker run --rm -e PORT=8001 \
  -e DATABASE_URL="postgresql+asyncpg://x:x@host.docker.internal/x" \
  -e GOOGLE_API_KEY=fake \
  -e ENVIRONMENT=development \
  -p 8001:8001 \
  job-agent-test &
sleep 5
curl -s http://localhost:8001/health
docker stop $(docker ps -q --filter ancestor=job-agent-test)
```
Expected: health endpoint responds (even if DB not available, at worst a 503, not a crash).

- [ ] **13d. Commit**

```bash
git add Dockerfile
git commit -m "chore: slim Dockerfile — remove WeasyPrint libs, listen on \$PORT"
```

---

## Task 14: Migrate LLM — update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **14a. In `pyproject.toml`, replace `langchain-anthropic>=0.3` with `langchain-google-genai>=2.0`:**

Find the line:
```toml
"langchain-anthropic>=0.3",
```
Replace with:
```toml
"langchain-google-genai>=2.0",
```

- [ ] **14b. Sync and verify install**

```bash
uv sync --dev
python -c "from langchain_google_genai import ChatGoogleGenerativeAI; print('ok')"
```
Expected: `ok`

- [ ] **14c. Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: replace langchain-anthropic with langchain-google-genai"
```

---

## Task 15: Create the test LLM shim

**Files:**
- Create: `app/agents/test_llm.py`

The shim is used by all `get_llm()` functions when `ENVIRONMENT=test`. It returns a `FakeListChatModel` whose responses can be customised per agent purpose.

- [ ] **15a. Create `app/agents/test_llm.py`**

```python
# app/agents/test_llm.py
"""
Test LLM shim: returned by get_llm() when ENVIRONMENT=test.
Each call to ainvoke/invoke cycles through the response list.
Subclasses / call sites can override responses for specific tests.
"""
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel


# Default canned responses by purpose — extend as needed in tests
_DEFAULT_RESPONSES: dict[str, list[str]] = {
    "onboarding": [
        "Hi! Tell me about the role you're looking for.",
        '{"target_title": "Software Engineer", "location": "Remote"}',
    ],
    "matching": [
        '{"score": 75, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}',
    ],
    "generation": [
        "Tailored resume content here.",
        "Tailored cover letter content here.",
    ],
    "resume_extraction": [
        '{"name": "Test User", "skills": ["Python"], "work_experience": []}',
    ],
}


def get_fake_llm(purpose: str = "matching") -> FakeListChatModel:
    responses = _DEFAULT_RESPONSES.get(purpose, ["fake response"])
    return FakeListChatModel(responses=responses)
```

- [ ] **15b. Run unit tests — confirm the module imports cleanly**

```bash
uv run python -c "from app.agents.test_llm import get_fake_llm; print(get_fake_llm())"
```
Expected: prints the FakeListChatModel object.

- [ ] **15c. Commit**

```bash
git add app/agents/test_llm.py
git commit -m "test: add test LLM shim for ENVIRONMENT=test (replaces mock_llm_server)"
```

---

## Task 16: Migrate `app/agents/onboarding.py` to Gemini

**Files:**
- Modify: `app/agents/onboarding.py`

- [ ] **16a. Replace the import at line 15**

Old:
```python
from langchain_anthropic import ChatAnthropic
```
New:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
```

- [ ] **16b. Replace the `get_llm()` function body**

Current (approximately lines 65–73):
```python
def get_llm() -> ChatAnthropic:
    settings = get_settings()
    return ChatAnthropic(
        model=settings.claude_model,
        api_key=settings.anthropic_api_key.get_secret_value(),
        base_url=settings.anthropic_base_url,
    )
```

Replace with:
```python
def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm
        return get_fake_llm("onboarding")
    return ChatGoogleGenerativeAI(
        model=settings.llm_generation_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )
```

- [ ] **16c. Verify no other Anthropic-specific calls remain in this file**

```bash
grep -n "anthropic\|claude\|cache_control" app/agents/onboarding.py
```
Expected: no matches.

- [ ] **16d. Run unit tests**

```bash
uv run pytest tests/unit/ -v 2>&1 | tail -10
```
Expected: all pass.

- [ ] **16e. Commit**

```bash
git add app/agents/onboarding.py
git commit -m "feat: migrate onboarding agent to Gemini"
```

---

## Task 17: Migrate `app/agents/matching_agent.py` to Gemini

**Files:**
- Modify: `app/agents/matching_agent.py`

- [ ] **17a. Replace the import**

Old:
```python
from langchain_anthropic import ChatAnthropic
```
New:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
```

- [ ] **17b. Replace `get_llm()` body**

```python
def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm
        return get_fake_llm("matching")
    return ChatGoogleGenerativeAI(
        model=settings.llm_matching_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )
```

- [ ] **17c. Verify `bind_tools` with `tool_choice` still works**

Find the line using `tool_choice="record_score"` (around line 115):
```python
llm = get_llm().bind_tools(tools, tool_choice="record_score")
```

`langchain-google-genai` supports `tool_choice` but the parameter name for forced function calling is `tool_choice` with value `"any"` or the tool name directly. Gemini's API uses `function_calling_config`. Verify: run a quick smoke test:

```bash
uv run python - <<'EOF'
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
import os
os.environ.setdefault("GOOGLE_API_KEY", "fake")  # won't actually call API

@tool
def record_score(score: int) -> str:
    """Record the score."""
    return str(score)

try:
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake")
    bound = llm.bind_tools([record_score], tool_choice="record_score")
    print("bind_tools with tool_choice OK:", bound)
except Exception as e:
    print("ERROR:", e)
EOF
```

If `tool_choice="record_score"` is not supported (will raise at init time), replace with `tool_choice="any"`:
```python
llm = get_llm().bind_tools(tools, tool_choice="any")
```
The `tool_choice="any"` forces the model to use a tool, which is the intent.

- [ ] **17d. Verify no other Anthropic references remain**

```bash
grep -n "anthropic\|claude\|cache_control" app/agents/matching_agent.py
```
Expected: no matches.

- [ ] **17e. Run unit tests**

```bash
uv run pytest tests/unit/ -v 2>&1 | tail -10
```

- [ ] **17f. Commit**

```bash
git add app/agents/matching_agent.py
git commit -m "feat: migrate matching agent to Gemini Flash"
```

---

## Task 18: Migrate `app/agents/generation_agent.py` to Gemini

**Files:**
- Modify: `app/agents/generation_agent.py`

- [ ] **18a. Replace the import**

Old:
```python
from langchain_anthropic import ChatAnthropic
```
New:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
```

- [ ] **18b. Replace `get_llm()` body**

```python
def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm
        return get_fake_llm("generation")
    return ChatGoogleGenerativeAI(
        model=settings.llm_generation_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )
```

- [ ] **18c. Verify no Anthropic references remain**

```bash
grep -n "anthropic\|claude\|cache_control" app/agents/generation_agent.py
```

- [ ] **18d. Commit**

```bash
git add app/agents/generation_agent.py
git commit -m "feat: migrate generation agent to Gemini Pro"
```

---

## Task 19: Migrate `app/services/resume_extraction.py` to Gemini

**Files:**
- Modify: `app/services/resume_extraction.py`

- [ ] **19a. Replace import and inline instantiation**

Old (around lines 12, 53–62):
```python
from langchain_anthropic import ChatAnthropic
...
llm = ChatAnthropic(
    model=settings.claude_matching_model,
    max_tokens=2048,
    api_key=settings.anthropic_api_key.get_secret_value(),
    base_url=settings.anthropic_base_url,
)
```

New:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
...
if settings.environment == "test":
    from app.agents.test_llm import get_fake_llm
    llm = get_fake_llm("resume_extraction")
else:
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_resume_extraction_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )
```

- [ ] **19b. Remove `max_tokens` — Gemini uses `max_output_tokens` as a generation config param, not a constructor param; for now leave it out (Gemini's default is sufficient for resume extraction).**

- [ ] **19c. Verify no Anthropic references remain**

```bash
grep -rn "anthropic\|claude\|ChatAnthropic" app/ --include="*.py"
```
Expected: zero matches.

- [ ] **19d. Commit**

```bash
git add app/services/resume_extraction.py
git commit -m "feat: migrate resume extraction service to Gemini Flash"
```

---

## Task 20: Update CI workflows — replace ANTHROPIC_API_KEY

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **20a. In `ci.yml`, replace every occurrence of `ANTHROPIC_API_KEY: test-key` with `GOOGLE_API_KEY: fake-test-key`**

There are four occurrences: unit tests (line 24), integration+e2e (line 28), migrations (line 79), Playwright (line 99).

Also remove the line `ANTHROPIC_BASE_URL: http://localhost:9000` from the Playwright job (line 100) — this was the mock server URL, which no longer exists.

- [ ] **20b. Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "chore: replace ANTHROPIC_API_KEY with GOOGLE_API_KEY in CI"
```

---

## Task 21: Remove the mock LLM server + update Playwright config

**Files:**
- Delete: `tests/e2e_helpers/mock_llm_server.py`
- Modify: `frontend/playwright.config.ts`

- [ ] **21a. Delete the mock server**

```bash
git rm tests/e2e_helpers/mock_llm_server.py
```

If `tests/e2e_helpers/` becomes empty after deletion:
```bash
git rm -r tests/e2e_helpers/
```

- [ ] **21b. Open `frontend/playwright.config.ts`. Remove the webServer entry for the mock LLM on port 9000.**

Current `webServer` array has three entries: mock LLM on :9000, FastAPI on :8000, Vite on :5173. Remove only the mock LLM entry.

Find and delete the block that looks like:
```ts
{
  command: 'cd .. && uv run python tests/e2e_helpers/mock_llm_server.py',
  port: 9000,
  reuseExistingServer: !process.env.CI,
},
```

Also remove the corresponding env var from the FastAPI webServer entry:
```ts
ANTHROPIC_BASE_URL: 'http://localhost:9000',
```

- [ ] **21c. Add `ENVIRONMENT=test` to the FastAPI webServer env in `playwright.config.ts`**

In the FastAPI webServer entry, add to its `env`:
```ts
ENVIRONMENT: 'test',
GOOGLE_API_KEY: 'fake-test-key',
```

This tells the app's `get_llm()` functions to return `FakeListChatModel` instead of calling Gemini.

- [ ] **21d. Run Playwright tests locally to verify the fake LLM works**

```bash
docker compose up -d db
uv run alembic upgrade head
cd frontend && npm run test:e2e 2>&1 | tail -30
```
Expected: all tests pass (or the same tests pass as before — if some were already failing due to assertion errors unrelated to LLM, document those separately).

- [ ] **21e. Commit**

```bash
git add tests/ frontend/playwright.config.ts
git commit -m "feat: replace mock_llm_server with in-process FakeListChatModel shim"
```

---

## Task 22: Full local test run — confirm clean state

- [ ] **22a. Run all backend tests**

```bash
uv run pytest tests/unit/ tests/integration/ tests/e2e/ -v 2>&1 | tail -30
```
Expected: all pass (integration tests require Docker for testcontainers — ensure Docker is running).

- [ ] **22b. Run lint**

```bash
uv run ruff check app/ tests/
```
Expected: no errors.

- [ ] **22c. Run frontend tests**

```bash
cd frontend && npm test && npm run build
```
Expected: Vitest passes, build succeeds.

- [ ] **22d. Fix any failures before continuing. Do not proceed to deploy tasks with failing tests.**

---

## Task 23: Write `docs/DEPLOYMENT.md` — provisioning reference

**Files:**
- Create: `docs/DEPLOYMENT.md`

This file is a permanent reference for anyone who forks the repo or needs to re-provision.

- [ ] **23a. Create `docs/DEPLOYMENT.md`** with the content from the Prerequisites section of this plan (P1–P6), formatted for a human reader with section headings, the exact commands, and callouts for the values that must be substituted.

Keep it short — just the provisioning commands with context, no tutorial prose.

- [ ] **23b. Commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: add DEPLOYMENT.md with GCP/Neon provisioning steps"
```

---

## Task 24: Create Cloud Run Jobs for migrations and seed

These are run once from the command line after first deploy, not from CI.

- [ ] **24a. Ensure the Docker image is already built and pushed (Task 26 wires this in CI; for the very first deploy you need to push manually or wait for the first CI deploy run)**

- [ ] **24b. Create the alembic migration Cloud Run Job**

```bash
IMAGE="us-central1-docker.pkg.dev/job-application-agent-portfolio/app/api:latest"

gcloud run jobs create alembic-migrate \
  --image "$IMAGE" \
  --region us-central1 \
  --command="alembic,upgrade,head" \
  --set-secrets="DATABASE_URL=database-url:latest"
```

- [ ] **24c. Create the seed Cloud Run Job**

```bash
gcloud run jobs create seed-demo \
  --image "$IMAGE" \
  --region us-central1 \
  --command="python,scripts/seed_demo_profile.py" \
  --set-secrets="DATABASE_URL=database-url:latest"
```

---

## Task 25: Create seed script + demo profile

**Files:**
- Create: `scripts/seed_demo_profile.py`
- Create: `demo_profile.json` (you fill in the content)

- [ ] **25a. Create `demo_profile.json`** — sanitised version of your real profile. Fill in:

```json
{
  "name": "Your Name",
  "email": "you@example.com",
  "target_title": "Senior Software Engineer",
  "target_location": "Remote",
  "skills": ["Python", "TypeScript", "FastAPI", "PostgreSQL", "AWS"],
  "years_experience": 5,
  "salary_min": 120000,
  "search_keywords": ["backend", "API", "Python"],
  "work_experience": [
    {
      "company": "Prev Company",
      "title": "Software Engineer",
      "start_date": "2021-01",
      "end_date": "2024-01",
      "bullets": ["Built X", "Led Y", "Improved Z by 30%"]
    }
  ]
}
```

- [ ] **25b. Create `scripts/seed_demo_profile.py`**

```python
#!/usr/bin/env python
"""
Idempotently upsert the demo profile under SINGLE_USER_ID.
Run once after first deploy: gcloud run jobs execute seed-demo --region us-central1 --wait
"""
import asyncio
import json
import uuid
from pathlib import Path

SINGLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def main():
    from app.config import get_settings
    from app.database import get_session_factory
    from app.services import profile_service

    profile_data = json.loads(Path("demo_profile.json").read_text())
    settings = get_settings()
    session_factory = get_session_factory()

    async with session_factory() as session:
        profile = await profile_service.get_or_create_profile(SINGLE_USER_ID, session)
        await profile_service.update_profile(profile.id, profile_data, session)
        await session.commit()
        print(f"Seeded profile {profile.id}")


if __name__ == "__main__":
    asyncio.run(main())
```

Note: `profile_service.get_or_create_profile` and `profile_service.update_profile` may have different signatures in the actual codebase. Read `app/services/profile_service.py` and adjust the calls to match.

- [ ] **25c. Commit**

```bash
git add scripts/seed_demo_profile.py demo_profile.json
git commit -m "feat: add seed script and demo profile for first deploy"
```

---

## Task 26: Wire deploy pipeline in ci.yml

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **26a. Add these jobs at the bottom of `ci.yml` (after `e2e-browser`)**

```yaml
  deploy:
    runs-on: ubuntu-latest
    needs: [test, frontend, e2e-browser]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v5

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}

      - name: Set up gcloud
        uses: google-github-actions/setup-gcloud@v2

      - name: Build and push image
        run: |
          gcloud builds submit \
            --tag us-central1-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID }}/app/api:${{ github.sha }} \
            --tag us-central1-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID }}/app/api:latest \
            .

      - name: Run migrations
        run: |
          IMAGE="us-central1-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID }}/app/api:${{ github.sha }}"
          if gcloud run jobs describe alembic-migrate --region us-central1 &>/dev/null; then
            gcloud run jobs update alembic-migrate --image "$IMAGE" --region us-central1
          else
            gcloud run jobs create alembic-migrate \
              --image "$IMAGE" --region us-central1 \
              --command="alembic,upgrade,head" \
              --set-secrets="DATABASE_URL=database-url:latest"
          fi
          gcloud run jobs execute alembic-migrate --region us-central1 --wait

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy api \
            --image us-central1-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID }}/app/api:${{ github.sha }} \
            --region us-central1 \
            --set-secrets="GOOGLE_API_KEY=google-api-key:latest,ADZUNA_APP_ID=adzuna-app-id:latest,ADZUNA_API_KEY=adzuna-api-key:latest,DATABASE_URL=database-url:latest,CRON_SHARED_SECRET=cron-shared-secret:latest,JWT_SECRET=jwt-secret:latest" \
            --set-env-vars="ENVIRONMENT=production,AUTH_ENABLED=false" \
            --min-instances=0 \
            --max-instances=1 \
            --allow-unauthenticated \
            --port=8000

      - name: Get Cloud Run URL
        id: url
        run: |
          URL=$(gcloud run services describe api --region us-central1 \
            --format="value(status.url)")
          echo "url=$URL" >> $GITHUB_OUTPUT

      - name: Smoke test
        run: |
          sleep 10
          uv sync --dev
          uv run pytest tests/smoke/ \
            --base-url="${{ steps.url.outputs.url }}" \
            -v 2>&1 | tail -30
```

- [ ] **26b. Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat: add Cloud Run deploy job to ci.yml, gated on main + green tests"
```

---

## Task 27: First push to main — watch the pipeline

- [ ] **27a. Push everything to main**

```bash
git push origin main
```

- [ ] **27b. Watch the Actions run**

```bash
gh run watch --exit-status
```
Or open `https://github.com/<you>/job-application-agent/actions` and watch the `CI` workflow run.

Expected stages: `test` → `frontend` → `e2e-browser` → `deploy` (only on main).

- [ ] **27c. If `deploy` fails** — check the log with:

```bash
gh run view --log-failed
```

Common failure modes:
- `Permission denied on Artifact Registry` — SA needs `artifactregistry.writer` role (re-check P5).
- `alembic-migrate job not found` — the job was not created (run Task 24 commands manually first with `gcloud auth login`).
- `Workload Identity Federation error` — re-check the pool + provider + principal binding from P5.

- [ ] **27d. Once deploy succeeds, get the Cloud Run URL**

```bash
gcloud run services describe api --region us-central1 --format="value(status.url)"
```

- [ ] **27e. Add the URL to GitHub secrets as `CLOUD_RUN_URL`** (for the cron workflow)

- [ ] **27f. Verify the production guard works**

```bash
URL=$(gcloud run services describe api --region us-central1 --format="value(status.url)")
curl -s -o /dev/null -w "%{http_code}" -X POST "$URL/api/test/seed"
```
Expected: `404`

---

## Task 28: Run seed and verify full flow

- [ ] **28a. Execute the seed job once**

```bash
gcloud run jobs execute seed-demo --region us-central1 --wait
```
Expected: `Execution seed-demo-xxxxx completed successfully`.

- [ ] **28b. Open the live URL in a browser and verify the full flow**

1. `$URL/` — landing page renders (or redirects to `/matches` since `AUTH_ENABLED=false`).
2. Onboarding chat is reachable at `$URL/profile`.
3. Send a message in the onboarding chat — response should come back (via Gemini).
4. Navigate to `/matches` — may be empty until a sync runs.
5. Manually trigger the sync cron endpoint:
   ```bash
   CRON_SECRET=$(gcloud secrets versions access latest --secret=cron-shared-secret)
   curl -sf -X POST -H "X-Cron-Secret: $CRON_SECRET" "$URL/internal/cron/sync"
   ```
6. Wait ~30 seconds and reload `/matches` — matches should appear.
7. Open a match card — verify the match detail renders.
8. Trigger generation queue:
   ```bash
   curl -sf -X POST -H "X-Cron-Secret: $CRON_SECRET" "$URL/internal/cron/generation-queue"
   ```
9. Reload the match — generation should produce a resume + cover letter draft.

- [ ] **28c. Document the live URL** in `README.md` (one line: `## Live demo — <URL>`). Commit.

```bash
git add README.md
git commit -m "docs: add live demo URL to README"
```

---

## Verification checklist

End of Phase 1. All of these must be true before starting Phase 2.

- [ ] `gh run list --limit 1` shows green for a commit on `main`.
- [ ] `curl $URL/health` returns 200.
- [ ] `curl -X POST $URL/api/test/seed` returns 404.
- [ ] `curl -X POST $URL/internal/cron/sync` (no header) returns 403.
- [ ] `curl -X POST -H "X-Cron-Secret: $CRON_SECRET" $URL/internal/cron/sync` returns 200.
- [ ] At least one match appears in the UI after triggering sync.
- [ ] Generation runs and produces a draft document.
- [ ] Docker image size is visibly smaller than before (check with `docker image ls job-agent-test`).
- [ ] `uv run ruff check app/ tests/` exits 0 locally.
- [ ] `uv run pytest tests/unit/ -v` all pass locally.
- [ ] `cd frontend && npm test` passes locally.

---

## Phase 2 next

Phase 2 (Google OAuth, data isolation, multi-user quotas, `BudgetExhausted` UX) gets a separate plan. Start it once the Phase 1 verification checklist is fully green.
