# Chat-driven company suggestions (Layer 3)

**Date:** 2026-05-08

**Status:** Draft for review

**Author:** Maksym Panibratenko (with Claude)

## Context

Layer 1 (PR #106) shipped multi-ATS company resolution: a `Company` entity, a JIT fan-out resolver across Greenhouse / Lever / Ashby, and a single text input on Settings.

Layer 2 (PR #107) shipped a hand-curated catalog: ~25 companies in `app/data/catalog/companies.yaml`, an idempotent boot-time seeder that flips `is_curated=true` on matching rows, a `GET /api/companies/catalog` endpoint, and a typeahead dropdown over the existing input.

Both layers solve the "user types a name" path. Neither helps the user when they don't already know which companies they want to follow. The chat agent (`app/agents/onboarding.py`) already accepts company adds via its `save_profile_updates` tool, but it has no view of the curated catalog and no signal about which curated companies fit the user's profile.

Layer 3 closes that gap with a deliberately small surface: one new chat tool that exposes the curated catalog, plus a single `tags` metadata field per row. The LLM uses the tags + its world knowledge of the company name + the live profile snapshot it already receives to propose suggestions in natural language. The user confirms; the existing `save_profile_updates({"target_companies": [...]})` path persists them.

The richer dimensions sketched in earlier conversations — embeddings, scoring services, ranking algorithms in code, structured industry/size enums, a "rationale persistence" trail, frontend tag-filter chips, an admin UI — are explicitly out of scope. Layer 3 is a chat-tool exposure of the catalog with one minimal metadata field, nothing more.

## Non-goals (deferred)

- A semantic-matching algorithm in code (embeddings, vector similarity, scoring service). The LLM is the ranker.
- Structured metadata enums (industry, size_bucket, hq_region). One free-form `tags: list[str]` field per row.
- Frontend changes. Typeahead doesn't surface tags. Settings UI unchanged. No tag filter chips.
- `GET /api/companies/catalog` change. Stays `[{id, canonical_name}]` as today.
- Admin UI for editing tags. Curator edits `companies.yaml` in PRs, same workflow as Layer 2.
- Nightly tag-validation. The only catalog cron remains `validate-catalog.yml` (provider-slug check).
- Persistence of suggestion rationales / "why did the agent pick X?" audit trail.
- Auto-add. The agent always proposes, the user always confirms.

## Decisions (locked during brainstorming)

| Topic | Choice |
|---|---|
| Chat agent role | Add by name + suggest as side-effect. Primary tool stays `save_profile_updates`. New read-only tool `list_curated_companies`. The LLM is the ranker. |
| Per-company metadata | Minimal: one `tags: list[str]` field, free-form. LLM uses tags + world knowledge of the name. |
| Catalog injection | Tool-call on demand. Agent calls `list_curated_companies()` only when the conversation calls for it. Zero token cost on profile-only turns. |
| Off-list resolution | Catalog-preferred via prompt; off-list allowed. If the agent proposes "Acme Corp", `save_profile_updates` runs the same Layer 1 fan-out the typeahead does — resolves or 404s back to the agent. |
| Tag vocabulary | Free-form. Common conventions documented in a YAML comment block (`ai`, `fintech`, `dev-tools`, `b2b`, `b2c`, `infra`, `early-stage`, `late-stage`, `public`, `remote-first`). LLM tolerates synonyms. |
| Persistence | `tags TEXT[] NOT NULL DEFAULT '{}'` column on `companies`, written by the existing `seed_catalog` upsert. Organic-resolution rows keep the empty array. |
| Tool surface | One new tool added to the existing `tools` list in `build_graph`. No changes to `save_profile_updates` itself. |

## Data model

### Schema change (one Alembic revision)

Add a `tags` array column to the existing `companies` table:

```sql
ALTER TABLE companies
    ADD COLUMN tags TEXT[] NOT NULL DEFAULT '{}';
```

No index. Tag-based filtering is not a query path Layer 3 introduces; the chat tool returns the full curated set. Existing rows (organic + curated) start with the empty array. The seed step on the next deploy fills curated rows from the YAML.

### Catalog source extension

`app/data/catalog/companies.yaml` gains an optional `tags` field per row:

```yaml
# Common tags (soft convention, not enforced):
#   sector:      ai · fintech · dev-tools · biotech · climate · gaming · social
#   shape:       b2b · b2c · infra · platform · marketplace
#   stage:       early-stage · late-stage · public
#   region/mode: remote-first · us · eu
# The agent reads tags as free text. Curator may add new tags as needed;
# synonyms like "fintech" / "finance" are tolerated by the LLM but cost
# clarity for the curator. Pick one and stick with it.
companies:
  - canonical_name: Linear
    providers:
      ashby: linear
    tags: [dev-tools, b2b, late-stage]
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
    tags: [fintech, infra, b2b, public]
  # ...
```

Rows without a `tags` field default to `[]`. The Pydantic parser accepts the absent case; it does not require a tag.

### Pydantic model

`app/services/company_catalog.py::CatalogRow` gains:

```python
class CatalogRow(BaseModel):
    canonical_name: str
    providers: CatalogProviderSlugs
    tags: list[str] = Field(default_factory=list)
```

Existing validators (`_has_at_least_one_provider`, `Catalog._no_duplicates`) unchanged.

### Company SQLModel

`app/models/company.py::Company` gains:

```python
tags: list[str] = Field(
    default_factory=list,
    sa_column=Column(ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
)
```

`ARRAY` import from `sqlalchemy.dialects.postgresql`. Matches the explicit-`sa_column` pattern the project's CLAUDE.md flags as required for ARRAY/JSONB.

## Seeder change

`seed_catalog` in `app/services/company_catalog.py`:

- Insert path adds `tags=row.tags`.
- ON CONFLICT DO UPDATE `set_` adds `"tags": row.tags`.
- Tags are NOT pre-reset for the whole table the way `is_curated` is. A row dropped from the YAML keeps its previous tags but flips `is_curated=false` — once `is_curated=false`, the chat tool's `WHERE is_curated` filter excludes it, so the stale tags never reach the LLM. Stale-but-invisible.

The reset-then-upsert single-transaction shape is unchanged. `session.expire_all()` after commit unchanged.

## Agent changes

### New tool

In `app/agents/onboarding.py::build_graph`, alongside the existing `save_profile_updates` tool:

```python
@tool
async def list_curated_companies(config: RunnableConfig) -> str:
    """Return the curated company catalog as a JSON array.
    Each entry has: canonical_name (str) and tags (list of strings).
    Call this when the user asks for company suggestions or to see what
    companies are available. Prefer suggesting from this list; you may
    suggest off-list names too — those get resolved against live ATS
    boards but won't have tags to reason over."""
    db_factory = config["configurable"]["db_factory"]
    async with db_factory() as session:
        rows = (
            await session.execute(
                select(Company.canonical_name, Company.tags)
                .where(Company.is_curated)
                .order_by(func.lower(Company.canonical_name))
            )
        ).all()
    payload = [{"canonical_name": r.canonical_name, "tags": list(r.tags)} for r in rows]
    return json.dumps(payload)
```

The tool reads via the same per-request session pattern `_fetch_profile_snapshot` already uses. It returns JSON the LangGraph tool node passes back to the agent unchanged.

`tools = [save_profile_updates, list_curated_companies]`. Both bound to the LLM via `llm.bind_tools(tools)` and registered with `ToolNode(tools)` as today.

### System prompt addendum

Append after the existing live-profile-snapshot block:

> When the user asks about company suggestions, what companies you can recommend, or asks you to find companies that match their profile: call `list_curated_companies` first to see what's available. Then propose 3 to 8 picks based on the user's `target_roles`, `seniority`, `search_keywords`, and `work_experiences`. Briefly explain each pick (one sentence is enough). Save the user's selections via `save_profile_updates({"target_companies": [...]})`. Prefer curated names; off-list names work but are slower and unverified.

## API

No changes. `GET /api/companies/catalog` keeps its current shape `[{id, canonical_name}]`. The frontend doesn't see tags. The chat tool reads from the DB directly inside the agent.

## Frontend

No changes. The typeahead, the chips, the settings layout, and the chat drawer all stay as-is. Tags exist only for the agent.

## Testing

### Unit

- `tests/unit/test_company_catalog_parse.py`:
  - Parse a row with `tags: [a, b]` — `row.tags == ["a", "b"]`.
  - Parse a row without `tags` — `row.tags == []`.
  - Parse `tags: []` — `row.tags == []`.

### Integration (testcontainers Postgres)

- `tests/integration/test_company_catalog_seed.py`:
  - Seed a YAML row with `tags: ["x", "y"]` — DB row's `tags` column equals `["x", "y"]`.
  - Idempotency: seed twice, second run leaves `tags` unchanged.
  - Drift: seed a row with tags, then re-seed an empty catalog — the row stays in DB, `is_curated` flips false, `tags` is unchanged (stale but invisible to the chat tool, which filters on `is_curated`).

- `tests/integration/test_onboarding_list_catalog_tool.py` (new):
  - Boot the onboarding agent. Inject a deterministic `FakeListChatModel` that emits a tool call to `list_curated_companies`, then a final reply.
  - Assert the tool's JSON return contains the seeded curated rows + their tags, ordered alphabetical, only `is_curated=true` rows.
  - Assert the tool does NOT include organic Company rows (those have `is_curated=false`).

### Migration

- `tests/integration/test_migration_companies.py` extension or a new `test_migration_tags.py`:
  - Apply the `add_companies_tags` migration on a fresh DB. `tags` column exists, type `text[]`, default `{}`, NOT NULL.
  - Downgrade drops the column cleanly.

## Rollout

- Single PR off main.
- Migration adds the `tags` column. Independent of any data change — safe to deploy.
- Seed runs on the same lifespan path. First boot post-deploy upserts curated rows with their tags; organic rows untouched.
- Nightly `validate-catalog.yml` continues unchanged (it doesn't read tags).
- Curator follows up with one or more YAML-only PRs to backfill tags on the existing 23 rows.
- The "no tags backfill" intermediate state is harmless: the agent calls `list_curated_companies`, gets an array of `{canonical_name, tags: []}`, and falls back entirely to its world knowledge of the names. Slightly less grounding, no breakage.

## Implementation shape

One PR. Estimated file list:

**Created:**
- `alembic/versions/<id>_add_companies_tags.py`
- `tests/integration/test_onboarding_list_catalog_tool.py`

**Modified:**
- `app/services/company_catalog.py` — `CatalogRow.tags`, seeder upsert.
- `app/data/catalog/companies.yaml` — add `tags:` to each existing row + comment block of conventions.
- `app/models/company.py` — `tags` field.
- `app/agents/onboarding.py` — register `list_curated_companies` tool, append to SYSTEM_PROMPT.
- `tests/unit/test_company_catalog_parse.py` — three new tests.
- `tests/integration/test_company_catalog_seed.py` — extend to assert tags persist + idempotent.

If the diff grows beyond what one PR can hold (it shouldn't), the natural split is "schema + parser + seed + YAML tags" first, then "tool + prompt" second. Default to one.
