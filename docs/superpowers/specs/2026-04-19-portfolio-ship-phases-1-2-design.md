# Portfolio-ready shipping: Phases 1 + 2 — design

**Status:** Design approved, awaiting implementation plan.
**Date:** 2026-04-19
**Repo:** `job-application-agent`

## Context

The repo is not currently shippable as a portfolio piece. Three concrete problems:

1. **CI is red on `main`.** Three independent causes: a 101-char line tripping `ruff`, Vitest picking up Playwright e2e specs because `vite.config.ts` has no `test.exclude`, and a cross-workflow `needs:` in `.github/workflows/deploy.yml` that GitHub Actions cannot resolve (jobs live in `ci.yml`).
2. **Nothing is deployed.** The project has a `fly.toml` but no Fly app provisioned and no successful deploy in history. The deploy workflow has never completed.
3. **Matching quality is thin.** Only two sources (Adzuna, JSearch), small page sizes, no user feedback loop, no fine-grained filters. Major aggregators like LinkedIn/Indeed are not covered.

This design covers the first two phases of a four-phase plan to reach a portfolio-credible state:

- **Phase 1 — Make it shippable.** Green CI, successful deploy to Cloud Run backed by Neon Postgres, Gemini LLM, scheduler externalised to GitHub Actions cron. Single seeded profile. Not yet shared publicly.
- **Phase 2 — Open it to users.** Google OAuth, data isolation, cost controls via GCP billing cap, graceful degradation when the monthly budget is hit. URL is safe to share.
- Phase 3 (matching quality, sources, feedback loop, eval harness) and Phase 4 (README, screenshots, polish) are out of scope for this spec — they get their own specs once Phase 2 is live.

### Product vision recap
Logged-in users sign in with Google, upload a resume, complete a conversational onboarding, and from then on the app matches them to jobs on a schedule. Users review pre-generated resumes + cover letters inline and submit applications (Greenhouse-backed where possible, otherwise a deep link).

### Constraints driving the design
- **Hosting budget: $0/mo infra.** Cloud Run free tier + Neon free tier.
- **LLM budget: $10/mo ceiling,** enforced by a GCP project budget cap.
- **APScheduler cannot run on a scale-to-zero host** — scheduler must be externalised.
- **Google infrastructure is the throughline** — hosting, LLM, and auth all anchor on Google to minimise the number of third parties.

---

## Phase 1 — Make it shippable

**Entry:** Current state of `main`.
**Exit:** `git push origin main` produces green CI + successful Cloud Run deploy + live URL at `https://job-application-agent-<hash>.run.app` serving a single seeded profile end-to-end. Not yet shared.

### 1.1 Fix CI

| Fix | Target |
|---|---|
| Wrap `app/agents/onboarding.py:201` below the 100-char limit | `ruff` passes |
| Add `test.exclude: ['e2e/**', 'node_modules/**', 'dist/**']` to `frontend/vite.config.ts` | Vitest stops importing `@playwright/test` and crashing |
| Upgrade `actions/checkout@v4`→`v5`, `actions/setup-node@v4`→`v5`, `astral-sh/setup-uv@v3`→`v5` in every workflow | Clears the Node 20 deprecation warning ahead of the June 2026 cutoff |
| Merge `.github/workflows/deploy.yml` into `ci.yml` as a final job gated on `if: github.ref == 'refs/heads/main' && success()` with `needs: [test, frontend, e2e-browser]` | Fixes the broken cross-workflow `needs:`; deploy only runs on green `main` |

### 1.2 Switch LLM: Anthropic → Gemini

Done upfront so the deploy is already on the target LLM. Scope:

- `pyproject.toml`: remove `langchain-anthropic`, add `langchain-google-genai>=2.0`.
- `app/agents/{onboarding,matching_agent,generation_agent}.py`: `get_llm()` factories return `ChatGoogleGenerativeAI`.
  - Onboarding + matching + resume extraction: `gemini-2.5-flash`.
  - Generation: `gemini-2.5-pro`.
- `app/services/resume_extraction.py`: switch model reference to Flash.
- Remove Anthropic `cache_control` blocks from prompt construction (Gemini's context-caching minimum is 32k tokens; our ~3k-token profile doesn't benefit, and Flash pricing makes it moot).
- Rename env var `ANTHROPIC_API_KEY` → `GOOGLE_API_KEY` in `.env.example`, `app/config.py`, all CI workflows, and the production secret.
- `frontend/playwright.config.ts`: remove the `webServer` entry that launches `mock_llm_server.py` on :9000, and remove the `ANTHROPIC_BASE_URL=http://localhost:9000` environment override.
- Test LLM shim: each agent's `get_llm()` checks `settings.environment == "test"` at call time and returns a `FakeListChatModel` pre-seeded from `app/agents/test_llm.py::fake_responses_for(purpose)`. `ENVIRONMENT=test` gets set in the Playwright workflow env and in `tests/e2e/conftest.py`. Delete the standalone mock LLM server file. Keeping per-agent `get_llm()` avoids refactoring all call sites; only the body changes.

### 1.3 Externalise the scheduler

APScheduler runs in-process today, which is incompatible with Cloud Run's scale-to-zero. Move the three scheduled jobs to HTTP endpoints gated by a shared secret, triggered by GitHub Actions cron.

**Endpoints** (all `POST`, all under `/internal/cron/*`, all require header `X-Cron-Secret: $CRON_SHARED_SECRET`, return 403 on mismatch):

| Endpoint | Current impl | Cron |
|---|---|---|
| `/internal/cron/sync` | `app/scheduler/tasks.py::run_job_sync` | every 4 h |
| `/internal/cron/generation-queue` | `app/scheduler/tasks.py::run_generation_queue` | every 10 min |
| `/internal/cron/maintenance` | `app/scheduler/tasks.py::run_daily_maintenance` | daily 03:00 UTC |

**Consequences:**

- Delete `app/scheduler/scheduler.py` and the APScheduler startup/shutdown in `app/main.py`'s lifespan.
- Drop `apscheduler` and `greenlet` from `pyproject.toml`.
- `app/scheduler/tasks.py` keeps the three async functions; they become the bodies of the new route handlers (in a new `app/api/internal_cron.py`).
- No more `apscheduler_jobs` table; no more APScheduler connection in the DB budget.
- New workflow `.github/workflows/cron.yml` with three `schedule:`-triggered jobs; each `curl`s its endpoint with the secret.

### 1.4 Provision Cloud Run + Neon (one-time)

User runs these once, then commits nothing except secret names.

```bash
# --- GCP ---
# Signup + billing already done per Phase 1 prerequisites.
gcloud projects create job-application-agent --set-as-default
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com
gcloud artifacts repositories create app --repository-format=docker --location=us-central1

# Secrets in Secret Manager
printf "%s" "$GOOGLE_API_KEY"    | gcloud secrets create google-api-key --data-file=-
printf "%s" "$ADZUNA_APP_ID"     | gcloud secrets create adzuna-app-id --data-file=-
printf "%s" "$ADZUNA_API_KEY"    | gcloud secrets create adzuna-api-key --data-file=-
printf "%s" "$DATABASE_URL"      | gcloud secrets create database-url --data-file=-
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create cron-shared-secret --data-file=-
printf "%s" "$(openssl rand -hex 32)" | gcloud secrets create jwt-secret --data-file=-

# Workload Identity Federation for GitHub Actions (no JSON key checked in).
# Follow Google's standard setup: https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines
# Briefly: create service account github-deployer, grant run.admin + secretmanager.secretAccessor +
# artifactregistry.writer + iam.serviceAccountUser on the project, create a workload-identity pool
# and OIDC provider bound to github.com/<user>/job-application-agent, allow the SA to be impersonated
# by that principal. Implementation plan covers the exact commands.
```

**Cloud Run Jobs needed (one-time creation before first deploy):**

```bash
# Migration job: runs alembic upgrade head against the same image
gcloud run jobs create alembic-migrate \
  --image us-central1-docker.pkg.dev/job-application-agent/app/api:latest \
  --region us-central1 \
  --command="alembic,upgrade,head" \
  --set-secrets="DATABASE_URL=database-url:latest"

# Demo-seed job: runs scripts/seed_demo_profile.py
gcloud run jobs create seed-demo \
  --image us-central1-docker.pkg.dev/job-application-agent/app/api:latest \
  --region us-central1 \
  --command="python,scripts/seed_demo_profile.py" \
  --set-secrets="DATABASE_URL=database-url:latest"
```

**Neon** (web UI, no CLI needed): create project → copy pooled connection string → convert driver prefix from `postgresql://` to `postgresql+asyncpg://` → store in GCP Secret Manager as `database-url`.

### 1.5 Dockerfile tightening

- Runtime base: `python:3.12-bookworm` → `python:3.12-slim-bookworm`. Saves ~500 MB.
- Remove `libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 shared-mime-info fonts-liberation` — unused since PDF switched to fpdf2.
- `CMD` listens on `$PORT`: `["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]`.
- Verify `fpdf2`, `psycopg[binary]`, `asyncpg` work on slim (all wheel/pure-Python, yes).

### 1.6 Production hardening

**`app/api/test_helpers.py`:** currently mounted behind a settings flag. Refactor to refuse mount if `settings.environment == "production"` — fail loud on import, not silent. Test: import app with `ENVIRONMENT=production`, assert `/api/test/seed` returns 404.

**`AsyncPostgresSaver.setup()`:** called in `app/main.py` lifespan today. Verify idempotence across cold starts (the underlying SQL uses `IF NOT EXISTS` for tables; `CREATE INDEX CONCURRENTLY` may error harmlessly on second call). If it's not fully idempotent, move it into the alembic migration step rather than the lifespan.

**Drop dead code:** remove empty `app/schemas/` package.

### 1.7 Deploy pipeline in CI

Final job in `ci.yml` (gated on `main` + all prior jobs green):

1. Authenticate to GCP via Workload Identity Federation.
2. Build image: `gcloud builds submit --tag us-central1-docker.pkg.dev/job-application-agent/app/api:$GITHUB_SHA`.
3. Run migration as a Cloud Run Job: `gcloud run jobs execute alembic-migrate --region us-central1 --wait`. If it fails, abort.
4. Deploy to Cloud Run. The full command mounts every secret listed in section 1.4 (`GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_API_KEY`, `DATABASE_URL`, `CRON_SHARED_SECRET`, `JWT_SECRET`) plus the non-secret env vars (`ENVIRONMENT=production`, `AUTH_ENABLED=false` in Phase 1, `AUTH_ENABLED=true` in Phase 2):
   ```bash
   gcloud run deploy api \
     --image us-central1-docker.pkg.dev/job-application-agent/app/api:$GITHUB_SHA \
     --region us-central1 \
     --set-secrets="GOOGLE_API_KEY=google-api-key:latest,ADZUNA_APP_ID=adzuna-app-id:latest,ADZUNA_API_KEY=adzuna-api-key:latest,DATABASE_URL=database-url:latest,CRON_SHARED_SECRET=cron-shared-secret:latest,JWT_SECRET=jwt-secret:latest" \
     --set-env-vars="ENVIRONMENT=production,AUTH_ENABLED=false" \
     --min-instances=0 --max-instances=1 --allow-unauthenticated
   ```
5. Smoke: `uv run pytest tests/smoke/ --base-url=https://api-<hash>-uc.a.run.app --has-seed-api=false`.

### 1.8 Seed my demo profile (one-off)

`scripts/seed_demo_profile.py` reads a committed `demo_profile.json` (sanitised) and idempotently upserts under `SINGLE_USER_ID`. Wired as a Cloud Run Job `seed-demo`, invoked manually once after the first deploy:

```bash
gcloud run jobs execute seed-demo --region us-central1 --wait
```

Not on every deploy.

### 1.9 Verifiable end state for Phase 1

1. `gh run list --limit 1` shows green on the first post-Phase-1 push.
2. `GET https://…run.app/health` returns 200.
3. `GET https://…run.app/api/test/seed` returns 404 (prod guard).
4. The three `.github/workflows/cron.yml` jobs appear in the Actions UI and run green on their first scheduled fire.
5. Full flow locally-against-prod: open UI → onboarding chat → manually curl `/internal/cron/sync` with secret → matches populate → open one → generation runs → review renders.
6. `gcloud run services describe api --region us-central1` shows `--min-instances=0 --max-instances=1`.

### 1.10 Phase 1 scope (out)
- Any auth, multi-user, or data isolation.
- Any matching-quality changes.
- README, screenshots, polish.
- Custom domain, Cloudflare.
- `min-instances=1` (accepts ~5 s cold-start pre-public).

---

## Phase 2 — Open it to users

**Entry:** Phase 1 deployed, single-user, Gemini LLM, scheduler on cron.
**Exit:** The URL is safe to share publicly. Google sign-in, per-user data isolation, monthly budget cap in GCP, graceful "budget reached" UX.

### 2.1 Google OAuth via fastapi-users

- Register OAuth 2.0 client in the existing GCP project. Authorised redirect: `https://…run.app/auth/google/callback`.
- Set `AUTH_ENABLED=true` in production.
- Wire `fastapi_users.get_oauth_router(google_oauth_client, auth_backend, settings.jwt_secret)` at `/auth/google`.
- `BearerTransport` + 24 h JWT. Frontend stores token in `sessionStorage`.
- `app/api/deps.py`: remove the 501 raise on `AUTH_ENABLED=true`, resolve `current_user` via `fastapi_users.current_user()`.
- Local dev unchanged: `AUTH_ENABLED=false` → hardcoded `SINGLE_USER_ID` path.

### 2.2 Data isolation audit

**Audit scope:** every place that reads or writes `user_profiles`, `applications`, `generation_runs`, LangGraph thread ids, `usage_counters` (new, see 2.5), `rate_limits` (new, see 2.6). Each must derive from `current_user.id`.

**Shared vs. per-user state:**

| Table | Scope | Enforcement |
|---|---|---|
| `jobs` | Shared across users | Sync writes once; no user filter needed on reads |
| `user_profiles` | Per user (`user_id` FK) | Add a service-level assert: `profile.user_id == current_user.id` |
| `applications` | Per profile (via `profile_id` FK to `user_profiles`) | Enforced by profile filter |
| `generation_runs` | Per application | Enforced transitively |
| LangGraph thread_ids | `thread_id = str(profile.id)` | Already per-user |

**Tests:** new `tests/integration/test_data_isolation.py` creates two users via the fastapi-users interface, runs matching for both, asserts neither can `GET /api/profile`, `GET /api/applications`, `POST /api/applications/{other_id}/submit` for the other's data. Must return 404 (not 403, to avoid leaking existence).

### 2.3 Cost strategy — externalised to GCP console

- **Monthly cap:** GCP billing budget on the project at $10/mo with alerts at $5 / $8 / $10.
- **Hard disable:** $10 alert wires to a Pub/Sub topic; a small Cloud Function subscribed to the topic calls `projects.disableBilling`. Documented in the spec appendix as optional — recommended for public demos.
- **No in-app cost counters.** No `daily_llm_spend` table, no `on_llm_end` callback.

### 2.4 Graceful degradation (`BudgetExhausted`)

- Single wrapper `app/agents/llm_safe.py::safe_ainvoke(model, prompt)` around LLM calls. Catches `google.api_core.exceptions.ResourceExhausted`, raises app-level `BudgetExhausted`.
- First time `BudgetExhausted` is raised, a marker row in new table `llm_status(id=1, exhausted_until TIMESTAMPTZ)` is written (next UTC month rollover).
- Route handlers catch `BudgetExhausted` → structured 503 with `{"reason": "budget_exhausted", "resumes_at": "..."}`.
- Cron handlers catch `BudgetExhausted` → log structured warning → continue non-LLM work (job collection) → skip matching/generation until `exhausted_until` passes.
- Frontend: new `GET /api/status` returns `{budget_exhausted: bool, resumes_at: datetime|null}`. The React app queries it on mount of every protected route via React Query (`staleTime: 60s`), and re-queries after any 503 response from an LLM-triggering action. If `budget_exhausted=true`, renders a top-of-page banner and disables "Run first sync now" + "Regenerate" buttons. No background polling loop.

### 2.5 Per-user throughput — via product design, not quotas

| User action | LLM cost | Guard |
|---|---|---|
| Edit profile text fields | $0 | Rate limit: 5 edits / hour / user |
| Upload new resume file | Flash extraction, ~$0.001 | 3 uploads / day / user; skip re-extraction if file SHA256 unchanged |
| Onboarding chat | Flash per message | Organic, user-paced |
| **"Run first sync now"** button | 1× fetch + matching + generation | **1 / day / user**; button enabled only if profile changed since last sync |
| View matches, apply, edit documents | $0 | default rate limit |

Scheduled work covers everything else. A `usage_counters(user_id, action, utc_day, count)` table (per-user, per-action, per-day) backs the quotas.

### 2.6 Rate limiter (app-layer, Postgres-backed)

- Middleware keyed by `user_id` (authenticated) or `X-Forwarded-For` first hop (IP).
- Single table `rate_limits(key TEXT, window_start TIMESTAMPTZ, count INT, UNIQUE(key, window_start))`.
- Defaults: unauth 20 req/min/IP, auth endpoints 5 req/min/IP, LLM-touching endpoints fall back to per-user action quotas above.
- Returns 429 with `Retry-After` header.

### 2.7 Abuse limits

- Resume upload ≤ 5 MB (FastAPI `Form` content-length check + MIME sniff).
- `work_experiences` ≤ 50 per profile.
- `applications` with `status='matched'` (unsubmitted) trimmed to the 500 most recent per user by the daily maintenance cron. Submitted applications (`status in ('submitted','rejected','offer')`) are kept indefinitely — user history should not disappear.

### 2.8 Frontend auth wiring

- New landing page at `/`: product pitch, "Sign in with Google" button linking to `/auth/google/authorize`, footer linking to GitHub repo.
- New `/auth/callback` route: reads `access_token` from query string → `sessionStorage` → redirects to `/profile` (if no profile) or `/matches`.
- New `<AuthProvider>` context: fetches `/api/users/me` on mount, exposes `{user, signOut}`; attaches `Authorization: Bearer …` to all API calls via React Query's `queryClient.defaultOptions`.
- Protected routes (`/matches`, `/profile`, `/applied`, `/matches/:id`): wrap in `<RequireAuth>`; redirect to `/` if no user.

### 2.9 Data migration

Drop the seeded demo profile as part of the `AUTH_ENABLED=true` migration. I re-onboard as myself on first Google sign-in. Avoids ownership-transfer fragility.

### 2.10 Verifiable end state for Phase 2

1. Unauthenticated visit to `https://…run.app` → landing page with Sign-in button, never 401s.
2. Two Google accounts → fully isolated data (integration test passes).
3. Induced `ResourceExhausted` in staging → banner shows, buttons disabled, cron keeps non-LLM work running.
4. 6 profile edits in an hour → 6th returns 429.
5. 2 "Run first sync now" presses same day → 2nd returns 429.
6. `/api/test/*` still 404 in prod.
7. `tests/smoke/` passes against prod with the new auth gate (uses a test account or bypasses auth via the test router — documented).

### 2.11 Phase 2 scope (out)
- Magic-link or email+password auth.
- BYO-key (per-user Gemini keys).
- Matching-quality changes (Phase 3).
- README polish, screenshots (Phase 4).
- Custom domain + Cloudflare (Phase 4).
- Refresh tokens (24 h JWT + re-login is enough for a demo).

---

## Risks and open questions

| Risk | Mitigation |
|---|---|
| `AsyncPostgresSaver.setup()` not idempotent under Cloud Run cold-starts → boot errors | Phase 1 includes a verification step. If non-idempotent, move into alembic migration. |
| Neon serverless cold-start + Cloud Run cold-start → 6–8 s first request | Documented trade-off; upgrade to `--min-instances=1` ($5/mo) if UX complaints arise. |
| GitHub Actions cron drifts up to 15 min from the declared schedule | Acceptable for 4 h / 10 min / daily cadences. |
| Gemini Flash sometimes returns non-strict JSON for structured scoring | Already handled by `ScoreResult` Pydantic coercion; re-verify after model swap and adjust prompts if failure rate spikes. |
| Playwright e2e mock replacement (FakeListChatModel) doesn't cover streaming paths | Phase 1 verification includes re-running all existing e2e specs. If streaming shape differs, extend the fake to emit message chunks. |
| Budget-disable Cloud Function is optional — without it, $10 is an alert, not a cap | Spec recommends wiring it. If skipped, user accepts overage risk. |
| Workload Identity Federation setup is fiddly first-time | Implementation plan walks through the exact commands (see Google's WIF-with-deployment-pipelines doc); fallback is a service-account JSON key stored as a GitHub secret, at the cost of a long-lived credential. |
| Gemini 2.5 Flash/Pro model names drift between releases | Pin the specific model version strings in `app/config.py` as settings fields (`llm_matching_model`, `llm_generation_model`) so bumping is a one-line change. Verify current stable model names at implementation time. |

## Success criteria (end of Phase 2)

A recruiter clicks the link, signs in with Google, uploads their resume, chats through onboarding, taps "Run first sync now", and sees scored matches + generated documents within ~60 seconds. They can apply to at least one Greenhouse-backed role without leaving the app. The URL stays alive for a full month without surprise billing, and if traffic spikes, the site degrades to "read-only matches, budget resumes 1st of next month" without crashing.
