# Curated company catalog (Layer 2)

**Date:** 2026-05-08
**Status:** Draft for review
**Author:** Maksym Panibratenko (with Claude)

## Context

Layer 1 of the multi-ATS work (`feat/provider-agnostic-companies`, PR #106) shipped a `Company` entity, a JIT fan-out resolver across Greenhouse / Lever / Ashby, and a single text input on the Settings page. Users type a company name, the backend hits all three boards in parallel, persists a `Company` row keyed on the normalized name, and adds it to `target_company_ids`.

Layer 1's UX has a cold-start gap: a new user has nothing to discover. The input is a blank text box. They have to know what they want to type. The original spec planned a Layer 2 follow-up to seed a curated catalog and surface it as a typeahead so users pick well-known companies from a list instead of guessing.

This spec covers Layer 2 with a deliberately minimal surface: a hand-curated YAML file ships ~50 companies, an idempotent boot-time seeder upserts them as `Company` rows flagged `is_curated=true`, and the existing `FollowedCompaniesSection.tsx` gains a typeahead dropdown that filters those rows client-side. Off-list inputs fall through to the existing Layer 1 fan-out path on Enter — no UX regression for users searching for long-tail companies.

The richer dimensions sketched in earlier conversations — per-company metadata (industry, stage, size, region), Layer 3 chat-driven semantic matching ("companies that hire similar profiles"), admin curation UI, organic-row promotion — are explicitly out of scope here. Layer 2 is a typeahead for curated names, nothing more.

## Non-goals (deferred to future specs)

- Per-company metadata fields (industry tag, size bucket, HQ region). Layer 2 stores only `canonical_name` + `provider_slugs`. Layer 3, when it lands, will need its own metadata pass.
- Layer 3 chat-driven semantic matching.
- Admin UI for catalog curation. Curation stays YAML-PR-based.
- Auto-promotion of organic Company rows (resolved via Layer 1 fan-out) into the curated set based on follower count or any popularity signal.
- A "request company" form for users when off-list resolution fails repeatedly. Telemetry on that flow would tell us if it matters; not building it now.
- Substring scoring / fuzzy matching beyond plain case-insensitive contains. The catalog is small enough that simple substring is fine.

## Decisions (locked during brainstorming)

| Topic | Choice |
|---|---|
| Data source | Hand-curated YAML file (one per repo, all providers in one file). Curator updates via PR. |
| Catalog size target | ~50 entries for v1 ship. Bigger sets are additive PRs. |
| Metadata per row | Minimal: `canonical_name` + per-provider `slug` map. No tagline / industry / size. |
| Seeding mechanism | Idempotent boot-time seed inside the FastAPI lifespan handler. Resets `is_curated=false` for every existing row, then upserts the YAML rows with `is_curated=true`. |
| Off-list flow | Auto-fanout on Enter. Dropdown shows "No matches — press Enter to search the boards" when filter has zero hits; Enter triggers the existing `POST /api/companies/resolve`. Catalog hits short-circuit through the resolver's cache (no httpx calls). |
| Validation | Nightly GitHub Actions workflow `validate-catalog.yml` hits each `(provider, slug)` against the real boards and opens an issue on consistent failures. |
| Typeahead source | Server returns the full curated list from `GET /api/companies/catalog`; frontend filters client-side. |
| Typeahead filtering | Case-insensitive substring on `canonical_name`. Top 8 results, alphabetical. Already-followed companies filtered by `id`. |
| Visibility | Authenticated users only (existing `Depends(get_current_profile)`). The data is public-shaped; the auth gate is consistency, not secrecy. |
| Existing `DEFAULT_SLUGS` | Migrated into the YAML as the starter set, then `app/data/default_slugs.py` is deleted. |

## Data model

### Schema change (one Alembic revision)

Add an `is_curated` flag to the existing `companies` table:

```sql
ALTER TABLE companies
    ADD COLUMN is_curated BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX ix_companies_is_curated ON companies(is_curated);
```

Existing rows (organic resolutions seeded by Layer 1's migration backfill) start at `false`. The boot-time seed flips the flag for YAML-listed rows.

The existing columns (`canonical_name`, `normalized_key UNIQUE`, `provider_slugs JSONB`, `unfollowable`, `resolved_at`, `created_at`) are unchanged.

### Catalog source file

Path: `app/data/catalog/companies.yaml`. Hand-curated, comments encouraged for human context. Schema:

```yaml
# Layer 2 catalog — companies surfaced in the Settings typeahead.
# Validate any new (provider, slug) pair against the real board before merging;
# the nightly validate-catalog cron alarms on dead entries but a curator who
# checks first avoids ever shipping breakage.
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
  - canonical_name: Linear
    providers:
      ashby: linear
  - canonical_name: Anthropic
    providers:
      greenhouse: anthropic
  # ... ~50 entries total, mix of well-known and high-quality smaller companies
```

Each row's `providers` map mirrors `app.sources.SOURCES` keys (`greenhouse` / `lever` / `ashby`). A row may list one or many providers depending on what the curator hand-validated.

The Pydantic model used for parsing:

```python
class CatalogProviderSlugs(BaseModel):
    greenhouse: str | None = None
    lever: str | None = None
    ashby: str | None = None

class CatalogRow(BaseModel):
    canonical_name: str
    providers: CatalogProviderSlugs

    @model_validator(mode="after")
    def _has_at_least_one_provider(self):
        if not any(getattr(self.providers, p) for p in ("greenhouse", "lever", "ashby")):
            raise ValueError(f"row {self.canonical_name!r} has no provider slugs")
        return self

class Catalog(BaseModel):
    companies: list[CatalogRow]
```

## Seeding

Service: `app/services/company_catalog.py::seed_catalog(session)`. Called once during FastAPI startup via the `lifespan` handler in `app/main.py`. Idempotent — every boot rebuilds the curated set:

1. Parse `app/data/catalog/companies.yaml` via Pydantic. Fail fast if the file is malformed, has duplicate `canonical_name` values, or has duplicate `normalized_key` values (after applying `_normalize` from `company_resolver`).
2. In a single transaction:
   - `UPDATE companies SET is_curated = false`. (Resets stale curated flags from a previous deploy.)
   - For each YAML row: `INSERT INTO companies (canonical_name, normalized_key, provider_slugs, is_curated, resolved_at, created_at) VALUES (...) ON CONFLICT (normalized_key) DO UPDATE SET canonical_name = EXCLUDED.canonical_name, provider_slugs = EXCLUDED.provider_slugs, is_curated = true`.
3. Commit. Log a structured message: `catalog.seeded count=N duration_ms=...`.

The `ON CONFLICT … DO UPDATE` semantic means a YAML row that matches an existing organic Company row (resolved earlier via Layer 1 fan-out) gets promoted to `is_curated=true` and refreshes its `provider_slugs` from the curated source. The Company's `id` stays stable, so any user already following that Company keeps the link.

A row dropped from the YAML on a later deploy → its `is_curated` flips to `false` after the next boot's reset step, but the Company row stays in the DB. Following users are unaffected.

The seed never deletes Company rows. Only the boolean flag flips.

## API

New endpoint: `GET /api/companies/catalog`. Returns the full curated list:

```json
[
  {"id": "uuid", "canonical_name": "Anthropic"},
  {"id": "uuid", "canonical_name": "Linear"},
  {"id": "uuid", "canonical_name": "Stripe"},
  ...
]
```

Server-side query: `SELECT id, canonical_name FROM companies WHERE is_curated = true ORDER BY LOWER(canonical_name)` — case-insensitive ordering so "anthropic" and "Anthropic" sort consistently regardless of the curator's casing.

- ~50 rows, ~3KB JSON.
- Authenticated (existing `Depends(get_current_profile)`). Per-user filtering does not apply; the data is identical for every caller.
- Response shape is stable; no pagination.

The endpoint lives in `app/api/companies.py` alongside the existing `POST /api/companies/resolve`.

## Frontend

`frontend/src/components/settings/FollowedCompaniesSection.tsx` gains a typeahead dropdown layered onto the existing input.

### Data loading

```ts
const { data: catalog = [] } = useQuery({
  queryKey: ['companies', 'catalog'],
  queryFn: api.getCompanyCatalog,
  staleTime: Infinity,
})
```

`staleTime: Infinity` because the catalog only changes on deploy. New `api.getCompanyCatalog()` helper in `frontend/src/api/client.ts`.

### Dropdown behavior

- **Open trigger:** input gains focus AND the user has typed at least one character. (Empty-string focus does not open the dropdown — keeps the empty-state visual clean.)
- **Filter:** `catalog.filter(c => c.canonical_name.toLowerCase().includes(draft.toLowerCase()))`, sliced to top 8 results, alphabetical within match.
- **Already-followed exclusion:** filter out rows whose `id` is already in `props.companies[].id`.
- **Row rendering:** just the canonical name (per the minimal-metadata decision).
- **Keyboard:** ↓/↑ moves the highlighted index, Enter selects the highlighted row, Esc closes the dropdown without selecting.
- **No-match state:** dropdown shows `No matches — press Enter to search the boards`. Pressing Enter then runs the existing `api.resolveCompany(draft)` flow exactly as today.
- **Catalog-hit click or Enter:** calls `api.resolveCompany(canonical_name)`. Since the row is already in `companies` (seeded), the resolver hits its cache path and returns immediately — no httpx fan-out, no provider validate calls. The chip is added optimistically; PATCH profile with the new id; rollback on PATCH failure (existing pattern).

### State machine (simplified)

```
[empty input]      → dropdown closed
[typing, has hits] → dropdown open, top 8 rows, ↓ highlights first
[typing, no hits]  → dropdown open, "No matches — press Enter to search the boards"
[Enter, has hit highlighted] → resolveCompany(highlighted.canonical_name) → optimistic chip → PATCH
[Enter, no highlight or no hits] → resolveCompany(draft) (Layer 1 fan-out)
[Esc] → close dropdown, keep draft
```

The existing optimistic-chip + rollback-on-PATCH-failure + 404/503 inline-error logic is preserved unchanged.

## Validation

Nightly GitHub Actions workflow: `.github/workflows/validate-catalog.yml`.

- Cron: `0 7 * * *` (UTC) — same window as other nightly jobs.
- Runs `uv run pytest tests/integration/test_catalog_live.py --catalog-live -v`.
- Test reads `companies.yaml` and calls `adapter.validate(slug)` against the real board for every `(provider, slug)` pair. No mocks.
- Failures collect into a single GitHub Issue (one issue per failed entry, deduped by title) tagged `catalog-validation`. Curator fixes via a follow-up YAML PR (drop the row, or replace the broken slug).
- The workflow does NOT block any PR. It's an alarm, not a gate.

## Testing

### Unit

- `tests/unit/test_company_catalog_parse.py` — YAML parsing, Pydantic validation, normalized_key generation, duplicate detection (canonical_name AND normalized_key).

### Integration (testcontainers Postgres)

- `tests/integration/test_company_catalog_seed.py`:
  - Seed runs on an empty DB, all YAML rows materialize as `is_curated=true`.
  - Seed is idempotent on re-run (no duplicate inserts, no flag flapping).
  - Drift handling: pre-seed an organic Company with the same normalized_key as a YAML row; run seed; assert the Company is upgraded to `is_curated=true` and `id` is unchanged.
  - Drift handling: drop a row from the YAML; run seed; assert `is_curated` flips to `false` and the Company row stays in the DB.
  - Malformed YAML raises a descriptive error at startup (test the parser invocation, not the lifespan handler — keeps the test focused).
- `tests/integration/test_companies_catalog_api.py`:
  - `GET /api/companies/catalog` returns only `is_curated=true` rows.
  - Response shape: list of `{id, canonical_name}` objects, alphabetical.
  - Unauthenticated requests return 401/403.
- Extend `tests/integration/test_company_resolution_flow.py`:
  - When the input matches a curated `canonical_name`, resolver short-circuits via cache. Verify zero httpx calls were issued (via `respx.mock` recording).

### Frontend (Vitest)

Extend `FollowedCompaniesSection.test.tsx`:

- Dropdown opens on focus + non-empty input.
- Substring filter shows expected rows; case-insensitive.
- Already-followed companies don't appear in dropdown.
- ↓ highlights first row; Enter selects highlighted row; calls `api.resolveCompany` with `canonical_name`.
- Enter with no matches calls `api.resolveCompany` with the raw input string (existing fall-through path).
- Esc closes the dropdown without selecting.
- Click on a dropdown row selects it (mouse parity with Enter).

### Live validation cron

- `tests/integration/test_catalog_live.py` (new file). Marked with a custom pytest mark (`@pytest.mark.catalog_live`) and only runs when `--catalog-live` is passed (registered alongside the existing `--has-seed-api` flag in `tests/conftest.py::pytest_addoption`). CI nightly invokes it; PR CI does not.

## Rollout

- Single PR off the merged Layer 1 base.
- Migration adds the `is_curated` column. Independent of any data changes — safe to deploy.
- App boot runs the seed via the FastAPI lifespan handler. Initial deploy populates the curated set atomically before the first request hits.
- Frontend PR includes inline screenshots of the new typeahead dropdown (per `feedback_frontend_pr_screenshots.md`).
- The existing `app/data/default_slugs.py` is deleted in this PR; `seed_defaults_if_empty` (already a no-op since Layer 1) is also deleted to remove the dead surface.
- The starter `companies.yaml` ships with the existing 15 `DEFAULT_SLUGS` migrated over plus ~35 curator-picked additions.
- Nightly `validate-catalog.yml` workflow lands in the same PR.
- Post-merge: monitor the post-merge run on `main` (deploy + smoke-prod). Watch the first nightly run of `validate-catalog.yml` for unexpected failures.
