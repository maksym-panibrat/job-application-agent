# Provider-agnostic companies (Lever + Ashby integration)

**Date:** 2026-05-08
**Status:** Draft for review
**Author:** Maksym Panibratenko (with Claude)

## Context

The frontend's "Target boards" section in `frontend/src/components/settings/TargetSlugsSection.tsx` exposes three providers — Greenhouse, Lever, Ashby — as if they were equal. Only Greenhouse is actually wired up. Slugs entered under Lever or Ashby are PATCHed onto `user_profiles.target_company_slugs` and silently dropped by every backend code path: the slug registry rejects non-`greenhouse_board` sources outright (`app/services/slug_registry_service.py:35-37`), `_prune_invalid_slugs` and `enqueue_stale` only read the `"greenhouse"` key, and the scheduler instantiates `GreenhouseBoardSource` directly with no provider dispatch (`app/scheduler/tasks.py:204-221`). Users get no error, no warning, no feedback.

A second issue surfaced during the audit: `Job.description_md` is misnamed. It stores the raw payload from the source (HTML for Greenhouse), and the markdownified version lives in `Job.description_clean`. The Greenhouse adapter compounds the confusion by markdownifying the HTML *before* handing it to the service layer (`app/sources/greenhouse_board.py:74`), which then runs `clean_html_to_markdown` over already-markdown content — a double-clean bug.

This spec lands the minimum to make the multi-provider UI honest and to clean up the description field naming. The provider concept disappears from the frontend entirely; users follow companies, not boards.

## Non-goals (deferred to future specs)

- **Layer 2** — curated catalog seed (top-N per provider with metadata) and typeahead UX. Companies in V1 resolve via on-demand fan-out only; no pre-built data file.
- **Layer 3** — chat-driven semantic matching ("companies that usually hire similar profiles"). Depends on Layer 2's metadata.
- **Cross-provider job dedup beyond `(source, external_id)`.** Companies pick one ATS at a time; the only multi-source overlap window is ATS migrations, which produce a small, bounded number of duplicate listings the user can tolerate seeing twice.
- **Pre-population of historical jobs** under the new `company_id` FK for jobs we never ingested (because no Greenhouse user followed them).

## Decisions (locked during brainstorming)

| Topic | Choice |
|---|---|
| Scope | Layer 1 only: Lever + Ashby adapters, `Company` entity, invisible-provider input, JIT fan-out resolution, description rename. |
| Input format | Single text field. User types a company name (`"Linear"`, `"Stripe"`, `"meta-platforms"`); resolver normalizes and fans out across all providers. |
| Provider visibility | None. Frontend never shows ATS provider; no per-provider tabs, no provider badges on chips. |
| Multi-match policy | Persist every provider that returned `200`. Scheduler fetches from all of them. Same role on two providers = two `Job` rows pointing at one `Company`. |
| Resolution miss | Return `None`. No "pending" placeholders. UI surfaces an inline error and the user retries. |
| Suffix variation guessing | Out. Tried in brainstorming, dropped as overkill for V1. Add later if data shows it's needed. |
| Description fields | Rename `description_md` → `description_raw` (untouched source payload), `description_clean` → `description` (canonical markdown). Greenhouse adapter stops pre-markdownifying. |
| Provider-name canonicalization | `Job.source` and `SlugFetch.source` migrate from `"greenhouse_board"` → `"greenhouse"`. New providers use bare names: `"lever"`, `"ashby"`. |
| Lever pagination | Adapter loops `?skip=X&limit=Y` internally until empty page. No `SlugFetch.cursor` plumbing. |
| Invalid-slug pruning | Relocates from `user_profile.target_company_slugs` to `Company.provider_slugs`. Profile intent is preserved even if all of a company's ATSs go bad. |
| Onboarding agent rework | Drop per-provider slug schema. Agent emits a flat list of company display names; backend resolver does the routing. |
| `target_company_slugs` removal | Deprecated, kept one release as rollback safety net, dropped in a follow-up migration. |

## Data model

### New: `companies` table

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` | PK, `gen_random_uuid()` default |
| `canonical_name` | `TEXT NOT NULL` | Display name, e.g. `"Linear"` |
| `normalized_key` | `TEXT NOT NULL UNIQUE` | Lowercase, internal whitespace → hyphens; used for dedup and lookup |
| `provider_slugs` | `JSONB NOT NULL` | `{greenhouse?: str, lever?: str, ashby?: str}`; at most one entry per provider |
| `unfollowable` | `BOOLEAN NOT NULL DEFAULT FALSE` | Set when every entry in `provider_slugs` has been marked invalid; surfaced as a soft warning, never auto-deletes |
| `resolved_at` | `TIMESTAMPTZ NOT NULL` | When fan-out last ran (refreshed on re-resolution) |
| `created_at` | `TIMESTAMPTZ NOT NULL` | `NOW()` default |

Indexes: `UNIQUE(normalized_key)`, GIN on `provider_slugs` for the migration's reverse-lookup query.

`provider_slugs` is JSONB rather than a separate `company_providers` table because the per-row entry count is small and bounded (one per supported ATS), queries are by company-id (not by-provider), and the JSONB shape matches the `JobSource` registry keys directly.

### Profile changes

```sql
ALTER TABLE user_profiles ADD COLUMN target_company_ids UUID[] NOT NULL DEFAULT '{}';
-- target_company_slugs JSONB stays for now; deprecated, dropped in a follow-up migration.
```

Application reads (matching, sync, onboarding-agent status) switch to `target_company_ids` immediately. No code path writes to `target_company_slugs` after this lands.

### Job changes

```sql
ALTER TABLE jobs ADD COLUMN company_id UUID NULL REFERENCES companies(id);
CREATE INDEX ix_jobs_company_id ON jobs(company_id);
ALTER TABLE jobs RENAME COLUMN description_md   TO description_raw;
ALTER TABLE jobs RENAME COLUMN description_clean TO description;
```

`Job.description_raw` holds the untouched source payload (HTML for Greenhouse and Lever and Ashby — all three return HTML from their public board endpoints). `Job.description` holds the markdown that matching, the LLM, embeddings, and the frontend all read.

`app/sources/greenhouse_board.py::_html_to_markdown` is deleted. The adapter sets `description_raw=item.get("content")` directly; the existing `clean_html_to_markdown` path in `app/services/job_service.py` produces `description` consistently for every source.

### `JobData` (Pydantic) mirrors the model

```python
class JobData(BaseModel):
    ...
    description_raw: str | None = None  # was: description_md
```

There is no `description` field on `JobData` — that's a service-layer derivation, not source data.

## Resolver

Service: `app/services/company_resolver.py::resolve(input: str, session) -> Company | None`.

### Algorithm

1. **Normalize:** `input.strip().lower()`, collapse internal whitespace to hyphens. `"Meta Platforms"` → `"meta-platforms"`. The normalized form is the candidate slug AND the `normalized_key` for cache lookup.
2. **Cache lookup:** `SELECT * FROM companies WHERE normalized_key = :key`. If hit, return immediately.
3. **Fan out:** in parallel via `asyncio.gather`, call `JobSource.validate(slug)` on every adapter in `SOURCES`. Each returns `(provider, found: bool)`.
4. **Persist matches:** for every provider that returned `True`, build `provider_slugs = {provider: slug for each match}`. Insert one `Company` row with `canonical_name = title-case(input)`, the normalized key, the provider slugs, `resolved_at = now()`. Use `INSERT … ON CONFLICT (normalized_key) DO NOTHING RETURNING *` to handle the concurrent-resolve race; on no-row-returned, re-`SELECT` by `normalized_key` and return that row.
5. **Zero matches:** return `None`. Caller surfaces the failure to the user.

### HTTP entrypoint

`POST /api/companies/resolve` body `{name: str}`:

- `200 { company: { id, canonical_name, providers: ["greenhouse", "ashby"] } }` on hit
- `404` on miss
- `503` on fan-out timeout (>3s wall) — caller retries on user action

The frontend uses this on form submit. Success path: optimistic chip render, then PATCH profile with the appended `company.id`.

### Multi-match behavior

Fan-out persists every confirming provider. The scheduler's `enqueue_stale` walks `Company.provider_slugs` and creates a `SlugFetch(source=provider, slug=slug)` per entry, so all confirmed providers are polled. `Job` rows are unique per `(source, external_id)`, so a Linear role posted on Ashby and Greenhouse during a migration window produces two distinct `Job` rows, both linked to the one `Company`. Both surface in the user's feed, both have valid `apply_url`s — the user can choose.

### Cost

Cache miss: parallel `GET /board/{slug}` probes across providers, ~200ms p50 wall. Cache hit: a single indexed `SELECT`. Cold lookups for a company nobody on the platform has ever followed pay the fan-out cost once, ever.

## Adapter dispatch

### Base class lift

`JobSource.validate(slug, *, client) -> bool` and `JobSource.fetch_jobs(slug, *, since, client) -> list[JobData]` move from `GreenhouseBoardSource` to the abstract base. `JobSource.search()` is unused in the slug-flow and is dropped. `JobSource.source_name` is renamed to `JobSource.provider_name` and now returns the bare provider key (`"greenhouse"`, `"lever"`, `"ashby"`).

### Source registry

`app/sources/__init__.py` becomes the single source of truth for which providers exist:

```python
from app.sources.greenhouse_board import GreenhouseBoardSource
from app.sources.lever_postings   import LeverPostingsSource
from app.sources.ashby_board      import AshbyBoardSource

SOURCES: dict[str, JobSource] = {
    "greenhouse": GreenhouseBoardSource(),
    "lever":      LeverPostingsSource(),
    "ashby":      AshbyBoardSource(),
}
```

`slug_registry_service.validate_slug` and `app/scheduler/tasks.run_sync_queue` look up `SOURCES[provider]` and call the lifted methods. The `validate_slug only supports greenhouse_board` raise dies. The resolver iterates `SOURCES.items()`.

### `enqueue_stale` rewrite

Today reads `profile.target_company_slugs["greenhouse"]`. New shape:

```python
companies = await session.execute(
    select(Company).where(Company.id.in_(profile.target_company_ids))
)
for company in companies.scalars():
    for provider, slug in company.provider_slugs.items():
        # upsert SlugFetch(source=provider, slug=slug) if stale or missing
```

### Invalid-slug pruning relocates

`_prune_invalid_slugs` is renamed to `_prune_invalid_provider_slugs(company, session)`. When the scheduler marks a `SlugFetch(source=provider, slug=slug)` as `is_invalid=True`, the corresponding key is dropped from `Company.provider_slugs`. If the dict empties, set `Company.unfollowable=True`. The user's `target_company_ids` is never touched — their intent is preserved across ATS migrations.

### Lever adapter specifics

Endpoint: `GET https://api.lever.co/v0/postings/{slug}?mode=json&skip=X&limit=Y`. Adapter loops internally: `skip = 0, limit = 100`, fetch, append, `skip += limit`, stop when an empty page returns. Same `since`-filter-client-side approach as Greenhouse. `description_raw` = `posting.descriptionHtml` from Lever's response (Lever provides both HTML and `descriptionPlain`; we always take HTML so the cleaner pipeline is uniform). `apply_url` = `posting.applyUrl`. `external_id` = `posting.id`.

### Ashby adapter specifics

Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`. No pagination. `description_raw` = `posting.descriptionHtml` (Ashby provides both formats; same uniformity argument). `apply_url` = `posting.applyUrl`. `external_id` = `posting.jobUrl` — Ashby's public board response does not expose a stable numeric id, but `jobUrl` is canonical per Ashby (one-to-one with the posting) and idempotent across fetches. We persist it verbatim with any query-string tracking params stripped.

## Migration

One Alembic revision lands the schema changes and the data backfill together, applied via `make migrate ARGS="upgrade head"` locally and the `migrate` CI job in production (per `CLAUDE.md`'s migration safety rules). Idempotent. A separate follow-up revision (described at the end of this section) drops the deprecated `target_company_slugs` column one release later.

### Schema steps

```sql
-- 1. companies table + indexes
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT NOT NULL,
    normalized_key TEXT NOT NULL UNIQUE,
    provider_slugs JSONB NOT NULL,
    unfollowable BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_companies_provider_slugs ON companies USING GIN (provider_slugs);

-- 2. profile column
ALTER TABLE user_profiles
    ADD COLUMN target_company_ids UUID[] NOT NULL DEFAULT '{}';

-- 3. job FK + description renames
ALTER TABLE jobs
    ADD COLUMN company_id UUID NULL REFERENCES companies(id);
CREATE INDEX ix_jobs_company_id ON jobs(company_id);
ALTER TABLE jobs RENAME COLUMN description_md   TO description_raw;
ALTER TABLE jobs RENAME COLUMN description_clean TO description;

-- 4. provider name normalization
UPDATE jobs         SET source = 'greenhouse' WHERE source = 'greenhouse_board';
UPDATE slug_fetches SET source = 'greenhouse' WHERE source = 'greenhouse_board';
```

### Data backfill

```sql
-- One Company row per unique greenhouse slug across all profiles
INSERT INTO companies (id, canonical_name, normalized_key, provider_slugs, resolved_at)
SELECT
    gen_random_uuid(),
    initcap(replace(slug, '-', ' ')),
    slug,
    jsonb_build_object('greenhouse', slug),
    NOW()
FROM (
    SELECT DISTINCT jsonb_array_elements_text(target_company_slugs->'greenhouse') AS slug
    FROM user_profiles
    WHERE jsonb_typeof(target_company_slugs->'greenhouse') = 'array'
) s
WHERE slug IS NOT NULL AND slug <> ''
ON CONFLICT (normalized_key) DO NOTHING;

-- Populate target_company_ids per profile
UPDATE user_profiles up
SET target_company_ids = COALESCE((
    SELECT array_agg(c.id)
    FROM jsonb_array_elements_text(up.target_company_slugs->'greenhouse') AS slug
    JOIN companies c ON c.provider_slugs->>'greenhouse' = slug
), '{}');

-- Backfill jobs.company_id from existing greenhouse jobs
UPDATE jobs j
SET company_id = c.id
FROM companies c
WHERE j.source = 'greenhouse'
  AND c.provider_slugs->>'greenhouse' IS NOT NULL
  AND c.canonical_name = j.company_name;
```

### Dropped data

`target_company_slugs.lever` and `target_company_slugs.ashby` entries are not migrated. Per the audit, the backend never read them — they were dead-input from the misleading UI. A one-shot `migration.dropped_dead_slugs` log captures the values for observability, then the data is gone. Users re-add by name after deploy and the resolver routes them correctly.

### Deferred-deploy hazard

The description-field renames (`description_md → description_raw`, `description_clean → description`) and the `source = 'greenhouse'` UPDATEs break old code on read. Mitigation: code change and migration ship in the same PR, gated by the `migrate` CI job, so the schema lands first and old code is gone in the same deploy. Standard Cloud Run deploy flow; no special handling.

### Post-deploy cleanup (follow-up migration, separate PR)

```sql
ALTER TABLE user_profiles DROP COLUMN target_company_slugs;
```

Held one release as a rollback safety net.

## Onboarding agent

`app/agents/onboarding.py` stops thinking in slugs.

- **System prompt:** rewrite step 4. "Learn which **companies** the user wants to follow" replaces "which Greenhouse slugs." Example payload changes from `{"greenhouse": ["stripe", "airbnb"], "lever": [], "ashby": []}` (line 218) to a flat `["Stripe", "Airbnb", "Linear"]`.
- **Tool schema:** `target_company_slugs` field is removed from the agent's `save_profile_updates` schema; replaced by `target_companies: list[str]` (display names).
- **`persist_inferred_slugs` → `persist_inferred_companies`:** for each name, call `company_resolver.resolve()`. Hits append `Company.id` to `profile.target_company_ids`. Misses log `onboarding.company_unresolved` and are skipped — the agent's transcript surfaces the dropped names if the user asks, but onboarding does not block on them.
- **Completion gate (line 75):** `profile.target_company_ids` non-empty replaces `target_company_slugs.greenhouse` non-empty. The "satisfies location gate but has zero greenhouse slugs → zero matches forever" failure mode keeps its semantics under the new field.
- **Status renderer (line 138):** prints `target_companies: [c.canonical_name for c in companies]` (companies loaded by id from the new table).

## Frontend

`frontend/src/components/settings/TargetSlugsSection.tsx` is renamed to `FollowedCompaniesSection.tsx`.

- **Single text input.** Placeholder: `"Add a company you want to follow"`. Submit on Enter.
- **Submit flow:** `POST /api/companies/resolve {name}` → spinner on the input. `200` → optimistic chip render with `canonical_name`, then PATCH profile with the appended id; on PATCH failure, roll the chip back and surface a toast (existing `useMutation onError` pattern in `TargetSlugsSection.tsx`). `404` → inline error: *"Couldn't find that company on any of our supported boards. Try the company name as it appears on their careers page, or paste the careers URL."* `503` → *"Couldn't reach our boards right now, try again."*
- **Chip rendering:** `{canonical_name}` only. No provider badge in V1. Remove button on each chip → PATCH profile with the id stripped.
- **Dead UI deleted:** the `PROVIDERS` array, per-provider `add()`/`remove()` keyed by provider, the three input rows.
- **API types (`frontend/src/api/client.ts`):** `target_company_slugs?: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }` removed. Profile read shape returns `target_companies: { id: string; canonical_name: string }[]` (resolved server-side via JOIN) so the frontend never makes a second round-trip. Profile PATCH accepts `target_company_ids: string[]`.
- **Section copy:** header `"Target boards"` → `"Followed companies"`. Subtitle: `"We'll match you to roles posted by these companies."`
- **Track events:** `settings.slug_added` → `settings.company_added`, `settings.slug_removed` → `settings.company_removed`. Payload `{company_id, canonical_name}`.

## Error handling and observability

### Resolver

- All-providers timeout (>3s wall): return `None`, frontend gets `503`. Log `company_resolver.fanout_timeout`.
- Mixed result (one provider `200`, others `5xx`): persist `Company` with the confirmed providers. Failed providers will get a normal `SlugFetch` retry on the next sync cycle and append themselves to `provider_slugs` if they confirm later. Log `company_resolver.partial_match` with the failing providers.
- Concurrent resolve race (two users resolving the same name): `INSERT … ON CONFLICT (normalized_key) DO NOTHING RETURNING`, then re-`SELECT` on no row.

### Adapters

`InvalidSlugError` and `TransientFetchError` lift from `app/sources/greenhouse_board.py` to `app/sources/base.py`. Lever and Ashby adapters raise the same types. The scheduler's existing branch logic in `run_sync_queue` works without modification.

### Log keys

- `company_resolver.cache_hit` / `.cache_miss` / `.match` / `.no_match` / `.partial_match` / `.fanout_timeout`
- `lever_postings.invalid_slug` / `.upstream_5xx` / `.network_error` (mirrors greenhouse's existing keys)
- `ashby_board.invalid_slug` / `.upstream_5xx` / `.network_error`
- `company.unfollowable` when a Company's last provider goes invalid
- `migration.dropped_dead_slugs` (one-shot, from the data backfill)

Routes through the existing `app/main.py::_add_cloud_run_severity` path to Cloud Error Reporting. No new SaaS.

### Rate limits

Lever: no documented `GET` limits. Ashby: undocumented. Greenhouse: no limits today and no observed throttling. The existing scheduler concurrency cap (`asyncio.Semaphore(8)` shared across the slug-fetch loop) stays as-is, shared across providers — not per-provider. Transient `429`s flow through the existing retry path in `run_sync_queue`. No new rate-limit logic in V1.

## Testing

### Unit

- `tests/unit/sources/test_lever_postings.py` and `tests/unit/sources/test_ashby_board.py`. Each adapter gets the same shape of canned-fixture tests the Greenhouse adapter already has: `validate` happy path, `validate` 404 → `False`, `fetch_jobs` happy path, 404 → `InvalidSlugError`, 5xx → `TransientFetchError`, network error → `TransientFetchError`, malformed JSON → `TransientFetchError`, jobs with no `apply_url` → skipped, `since` filter applied client-side. Lever's pagination loop gets a multi-page fixture.
- `tests/unit/services/test_company_resolver.py`: cache hit, full miss, single-provider hit, multi-provider hit, partial-failure path, `IntegrityError` on concurrent insert handled, normalization applied (case, whitespace, hyphenation).

### Integration (testcontainers Postgres)

- `tests/integration/test_company_resolution_flow.py`: full roundtrip through `POST /api/companies/resolve`. Adapters mocked at the `httpx` layer (no real ATS calls). One test per provider hit, plus the multi-match case, plus the all-miss → `404`, plus the timeout → `503`.
- `tests/integration/test_migration_companies.py`: seed pre-migration profile JSON with `{"greenhouse": ["stripe", "airbnb"], "lever": ["dead-entry"]}`, run the Alembic upgrade, assert: `Company` rows materialized for the two greenhouse slugs, `target_company_ids` populated, `lever` entries dropped and logged, `jobs.company_id` backfilled where applicable, `jobs.source` migrated `'greenhouse_board'` → `'greenhouse'`.

### Smoke (real server, `--has-seed-api`)

- `tests/smoke/test_company_resolution.py`: seed a profile, `POST /api/companies/resolve {"name": "Stripe"}`, expect `200` and a `Company` row materialized, then `GET /api/profile` and assert `Stripe` appears under `target_companies`. The adapter HTTP layer is the only piece not mocked — we accept the smoke test depending on the real Greenhouse public board being up.

### Out of test scope

No tests against the real Lever or Ashby APIs. Those would be flaky and rate-limit-bound; the existing Greenhouse adapter is also adapter-mocked in unit and integration tiers and that's been fine.

## Rollout

- One PR for the schema migration + backend code + frontend code (description renames force them together).
- Merge → `main` runs the `migrate` CI job, then deploy + smoke-prod (per `CLAUDE.md`: monitor the post-merge run, not just the PR run).
- One release on, ship the follow-up PR that drops `user_profiles.target_company_slugs`.
- Frontend PR includes inline screenshots of the new "Followed companies" section (per memory `feedback_frontend_pr_screenshots.md`).
