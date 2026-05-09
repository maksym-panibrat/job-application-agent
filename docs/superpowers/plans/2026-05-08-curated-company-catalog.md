# Curated Company Catalog (Layer 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hand-curated company catalog (~50 entries in `app/data/catalog/companies.yaml`), seeded into the `companies` table at app boot, exposed at `GET /api/companies/catalog`, and surfaced as a typeahead dropdown on `FollowedCompaniesSection.tsx`. Off-list inputs still fall through to Layer 1's fan-out resolver.

**Architecture:** A new `is_curated BOOLEAN` column on `companies` distinguishes catalog rows from organic-resolution rows. `app/services/company_catalog.py::seed_catalog()` runs on FastAPI startup (idempotent — resets the flag, then upserts every YAML row with `is_curated=true`). The frontend loads the full curated list once via TanStack Query and filters client-side; catalog hits short-circuit through the resolver's cache (zero httpx calls), off-list inputs trigger the existing Layer 1 fan-out on Enter. A nightly GitHub Actions workflow validates every `(provider, slug)` pair against the real boards.

**Tech Stack:** Python 3.12, FastAPI + SQLModel + Alembic + Postgres, Pydantic v2 + PyYAML for the catalog parser, structlog. Frontend: React + TypeScript + Vite + TanStack Query. Tests: pytest + testcontainers + Vitest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-08-curated-company-catalog-design.md`.

**Prerequisites:** PR #106 (`feat/provider-agnostic-companies`, the Layer 1 work) must be merged into `main` before this plan executes. The `Company` model, `target_company_ids` field, `company_resolver`, `POST /api/companies/resolve` endpoint, `SOURCES` registry, and `FollowedCompaniesSection.tsx` all need to exist.

---

## File map

### Created
- `app/data/catalog/__init__.py` — empty package marker.
- `app/data/catalog/companies.yaml` — hand-curated catalog source. ~25 starter entries (the existing 15 `DEFAULT_SLUGS` migrated + ~10 well-known multi-provider examples). Curator grows the set in follow-up YAML-only PRs.
- `app/services/company_catalog.py` — Pydantic models for parsing the YAML + `seed_catalog(session)` idempotent seeder.
- `tests/unit/test_company_catalog_parse.py` — YAML parsing + duplicate detection tests.
- `tests/integration/test_company_catalog_seed.py` — `seed_catalog` runs, idempotent on re-run, drift handling (rows added/removed flip `is_curated`).
- `tests/integration/test_companies_catalog_api.py` — `GET /api/companies/catalog` shape + auth tests.
- `tests/integration/test_catalog_live.py` — real-board validation behind the `--catalog-live` flag.
- `tests/integration/_catalog_fixtures/empty.yaml`, `tests/integration/_catalog_fixtures/two_rows.yaml`, `tests/integration/_catalog_fixtures/duplicate_keys.yaml` — isolated YAML fixtures so tests don't depend on the production file.
- `alembic/versions/<id>_add_companies_is_curated.py` — single Alembic revision adding the `is_curated` column + index.
- `.github/workflows/validate-catalog.yml` — nightly cron that runs `pytest tests/integration/test_catalog_live.py --catalog-live`.

### Modified
- `app/main.py` — wire `seed_catalog(session)` into the FastAPI `lifespan` handler after the checkpointer setup, before the `yield`.
- `app/api/companies.py` — add `GET /api/companies/catalog` alongside the existing `POST /api/companies/resolve`.
- `tests/conftest.py` — register the `--catalog-live` pytest CLI flag alongside the existing `--has-seed-api` flag.
- `frontend/src/api/client.ts` — add `getCompanyCatalog()` helper returning `Array<{id, canonical_name}>`.
- `frontend/src/components/settings/FollowedCompaniesSection.tsx` — layer a typeahead dropdown over the existing input. Catalog hit on Enter / click → existing resolve flow with the canonical name; no-match → existing Layer 1 fan-out fall-through.
- `frontend/src/components/settings/FollowedCompaniesSection.test.tsx` — extend with typeahead-specific tests.

### Deleted
- `app/data/default_slugs.py` — superseded by `companies.yaml`.
- Any `app/services/profile_service.py::seed_defaults_if_empty` definition (already a no-op since Layer 1 D4) and any callers — now-dead surface.
- `tests/integration/test_default_slugs_live.py` — replaced by `test_catalog_live.py`.

---

# Track A — Schema + parser

### Task A1: Alembic migration for `companies.is_curated`

**Files:**
- Create: `alembic/versions/<id>_add_companies_is_curated.py`

- [ ] **Step 1: Generate the revision scaffold**

Run: `make migrate ARGS='revision -m add_companies_is_curated'`
Expected: a new file under `alembic/versions/<hash>_add_companies_is_curated.py`. Note its filename and revision id (call it `<NEW>`); the previous head should be the Layer 1 migration (`bf8093d778c9` — call it `<PREV>` and confirm via `make migrate ARGS="heads"`).

Note: NOT autogenerate — the model change in Task A2 hasn't landed yet, so autogenerate would produce no diff. We hand-write the migration body.

- [ ] **Step 2: Hand-write the migration body**

Open the generated file. Replace its body (keep the autogenerated `revision` and `down_revision` identifiers — fix `down_revision` to point at the Layer 1 head if autogenerate guessed wrong):

```python
"""add companies.is_curated

Revision ID: <NEW>
Revises: <PREV>
Create Date: 2026-05-08 ...
"""

import sqlalchemy as sa
from alembic import op

revision = "<NEW>"
down_revision = "<PREV>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "is_curated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_companies_is_curated", "companies", ["is_curated"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_companies_is_curated", table_name="companies")
    op.drop_column("companies", "is_curated")
```

- [ ] **Step 3: Apply the migration locally**

Run: `make migrate ARGS="upgrade head"`
Expected: migration applies cleanly. Confirm via `psql -h localhost -U jobagent -d jobagent -c "\d companies"` — `is_curated` column should appear.

- [ ] **Step 4: Verify the existing test suite still passes**

Run: `uv run pytest tests/integration/test_company_resolution_flow.py tests/integration/test_company_resolver.py -v`
Expected: PASS — Layer 1 tests don't reference `is_curated` so they should be unaffected.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/<the-generated-filename>
git commit -m "feat(catalog): add companies.is_curated column

Single Alembic revision adding a boolean flag distinguishing curated
catalog rows from organic Layer 1 fan-out resolutions. Defaults to false
so existing rows are unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task A2: Catalog parser (Pydantic models)

**Files:**
- Create: `app/services/company_catalog.py`
- Test: `tests/unit/test_company_catalog_parse.py`

This task adds the YAML parser ONLY. The seed function lands in Task B2.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_company_catalog_parse.py`:

```python
"""Tests for catalog YAML parsing + validation."""

import pytest

from app.services.company_catalog import (
    Catalog,
    CatalogRow,
    parse_catalog,
)


def test_parse_minimal_row():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
"""
    catalog = parse_catalog(raw)
    assert isinstance(catalog, Catalog)
    assert len(catalog.companies) == 1
    row = catalog.companies[0]
    assert row.canonical_name == "Stripe"
    assert row.providers.greenhouse == "stripe"
    assert row.providers.lever is None
    assert row.providers.ashby is None


def test_parse_multi_provider_row():
    raw = """
companies:
  - canonical_name: Acme
    providers:
      greenhouse: acme-eng
      lever: acme
      ashby: acme
"""
    catalog = parse_catalog(raw)
    row = catalog.companies[0]
    assert row.providers.greenhouse == "acme-eng"
    assert row.providers.lever == "acme"
    assert row.providers.ashby == "acme"


def test_parse_rejects_row_with_no_providers():
    raw = """
companies:
  - canonical_name: NoProvider
    providers: {}
"""
    with pytest.raises(ValueError, match="no provider slugs"):
        parse_catalog(raw)


def test_parse_rejects_duplicate_canonical_name():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
  - canonical_name: Stripe
    providers:
      greenhouse: stripe-other
"""
    with pytest.raises(ValueError, match="duplicate canonical_name"):
        parse_catalog(raw)


def test_parse_rejects_duplicate_normalized_key():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
  - canonical_name: stripe
    providers:
      ashby: stripe
"""
    # Different canonical_name casing collapses to the same normalized_key.
    with pytest.raises(ValueError, match="duplicate normalized_key"):
        parse_catalog(raw)


def test_parse_rejects_malformed_yaml():
    with pytest.raises(ValueError):
        parse_catalog("not: valid: yaml: at: all")


def test_normalized_key_lowercases_and_hyphenates():
    """Catalog parser uses the same normalization as the resolver: trim,
    lowercase, collapse internal whitespace runs to hyphens."""
    raw = """
companies:
  - canonical_name: Meta Platforms
    providers:
      greenhouse: meta
"""
    catalog = parse_catalog(raw)
    assert catalog.companies[0].normalized_key == "meta-platforms"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_company_catalog_parse.py -v`
Expected: FAIL — `app.services.company_catalog` doesn't exist yet.

- [ ] **Step 3: Implement `app/services/company_catalog.py`**

```python
"""Catalog YAML parser + (in Task B2) idempotent seeder.

The catalog source is hand-curated at app/data/catalog/companies.yaml.
Each row maps to one Company entity (one row per company across all the
ATSs it appears on). The parser enforces:
  - at least one provider slug per row
  - unique canonical_name across the file
  - unique normalized_key across the file (so two casings of the same name
    don't collide at INSERT time)
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, model_validator

from app.services.company_resolver import _normalize


class CatalogProviderSlugs(BaseModel):
    greenhouse: str | None = None
    lever: str | None = None
    ashby: str | None = None


class CatalogRow(BaseModel):
    canonical_name: str
    providers: CatalogProviderSlugs

    @property
    def normalized_key(self) -> str:
        return _normalize(self.canonical_name)

    @property
    def provider_slugs_dict(self) -> dict[str, str]:
        """Flatten the typed providers into the dict shape stored on
        Company.provider_slugs (only present keys included)."""
        out: dict[str, str] = {}
        for k in ("greenhouse", "lever", "ashby"):
            v = getattr(self.providers, k)
            if v:
                out[k] = v
        return out

    @model_validator(mode="after")
    def _has_at_least_one_provider(self) -> "CatalogRow":
        if not self.provider_slugs_dict:
            raise ValueError(f"row {self.canonical_name!r} has no provider slugs")
        return self


class Catalog(BaseModel):
    companies: list[CatalogRow]

    @model_validator(mode="after")
    def _no_duplicates(self) -> "Catalog":
        names: dict[str, int] = {}
        keys: dict[str, int] = {}
        for i, row in enumerate(self.companies):
            if row.canonical_name in names:
                raise ValueError(
                    f"duplicate canonical_name {row.canonical_name!r} at "
                    f"rows {names[row.canonical_name]} and {i}"
                )
            names[row.canonical_name] = i
            if row.normalized_key in keys:
                raise ValueError(
                    f"duplicate normalized_key {row.normalized_key!r} at "
                    f"rows {keys[row.normalized_key]} and {i} "
                    f"(canonical_names: {self.companies[keys[row.normalized_key]].canonical_name!r}, "
                    f"{row.canonical_name!r})"
                )
            keys[row.normalized_key] = i
        return self


def parse_catalog(raw: str) -> Catalog:
    """Parse YAML text → Catalog. Raises ValueError on any structural,
    duplicate, or empty-providers error."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"catalog must be a mapping at the top level, got {type(data).__name__}")
    return Catalog.model_validate(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_company_catalog_parse.py -v`
Expected: PASS — all 7 tests pass.

- [ ] **Step 5: Verify PyYAML is installed**

Run: `uv run python -c "import yaml; print(yaml.__version__)"`
Expected: a version printed. PyYAML is a transitive dep of LangChain, so it should already be present. If not, run `uv add pyyaml`.

- [ ] **Step 6: Commit**

```bash
git add app/services/company_catalog.py tests/unit/test_company_catalog_parse.py
git commit -m "feat(catalog): YAML parser with duplicate detection

Pydantic-backed parser for app/data/catalog/companies.yaml. Enforces:
  - at least one provider slug per row,
  - unique canonical_name across the file,
  - unique normalized_key across the file (casing collapse caught).

The parser is independent of the seed step (Task B2) so it can be reused
by the live-validation cron and by future curator tooling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Track B — Catalog source + seeder

### Task B1: Initial `companies.yaml`

**Files:**
- Create: `app/data/catalog/__init__.py` (empty marker)
- Create: `app/data/catalog/companies.yaml`

This is a content task. The implementer migrates the existing `DEFAULT_SLUGS` (15 Greenhouse-only entries) plus adds ~10 well-known multi-provider examples to demonstrate the multi-ATS shape. The curator (user) extends the catalog in follow-up YAML-only PRs.

- [ ] **Step 1: Create the package marker**

Create `app/data/catalog/__init__.py` as an empty file (one blank line).

- [ ] **Step 2: Write `app/data/catalog/companies.yaml`**

```yaml
# Layer 2 catalog — companies surfaced in the Settings typeahead.
#
# Validate any new (provider, slug) pair against the real board before merging:
#   curl -sI https://boards-api.greenhouse.io/v1/boards/{slug}
#   curl -sI https://api.lever.co/v0/postings/{slug}
#   curl -sI https://api.ashbyhq.com/posting-api/job-board/{slug}
# The nightly validate-catalog cron alarms on dead entries but a curator who
# checks first avoids ever shipping breakage.
#
# canonical_name MUST be unique across the file. normalized_key (lowercase +
# whitespace collapsed to hyphens) MUST also be unique — the parser rejects
# both kinds of duplicate at startup.
companies:
  # — Migrated from app/data/default_slugs.py (Layer 1 starter set) —
  - canonical_name: Airbnb
    providers:
      greenhouse: airbnb
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
  - canonical_name: Dropbox
    providers:
      greenhouse: dropbox
  - canonical_name: Vercel
    providers:
      greenhouse: vercel
  - canonical_name: Instacart
    providers:
      greenhouse: instacart
  - canonical_name: Gusto
    providers:
      greenhouse: gusto
  - canonical_name: Robinhood
    providers:
      greenhouse: robinhood
  - canonical_name: DoorDash
    providers:
      greenhouse: doordashusa
  - canonical_name: Scale AI
    providers:
      greenhouse: scaleai
  - canonical_name: Ramp
    providers:
      greenhouse: rampnetwork
  - canonical_name: Anthropic
    providers:
      greenhouse: anthropic
  - canonical_name: Samsara
    providers:
      greenhouse: samsara
  - canonical_name: Datadog
    providers:
      greenhouse: datadog
  - canonical_name: Cloudflare
    providers:
      greenhouse: cloudflare
  - canonical_name: Asana
    providers:
      greenhouse: asana
  # — Multi-provider / Lever / Ashby examples (curator additions) —
  - canonical_name: Linear
    providers:
      ashby: linear
  - canonical_name: Notion
    providers:
      greenhouse: notion
  - canonical_name: Figma
    providers:
      greenhouse: figma
  - canonical_name: OpenAI
    providers:
      greenhouse: openai
  - canonical_name: GitHub
    providers:
      greenhouse: github
  - canonical_name: Brex
    providers:
      greenhouse: brex
  - canonical_name: Mercury
    providers:
      ashby: mercury
  - canonical_name: Replicate
    providers:
      ashby: replicate
  - canonical_name: Posthog
    providers:
      ashby: posthog
  - canonical_name: Modal
    providers:
      ashby: modal
```

Note: `doordashusa` and `rampnetwork` keep their Greenhouse slug (the old `DEFAULT_SLUGS` value) but the canonical_name uses the user-facing brand ("DoorDash", "Ramp"). This is the whole point of the curated catalog — slug noise stays internal, name presentation stays human.

- [ ] **Step 3: Sanity-parse the file**

Run: `uv run python -c "from pathlib import Path; from app.services.company_catalog import parse_catalog; c = parse_catalog(Path('app/data/catalog/companies.yaml').read_text()); print(f'parsed {len(c.companies)} entries: {[r.canonical_name for r in c.companies]}')"`
Expected: 25 entries listed, no errors.

- [ ] **Step 4: Commit**

```bash
git add app/data/catalog/
git commit -m "feat(catalog): initial companies.yaml with 25 starter entries

15 entries migrated from app/data/default_slugs.py (Layer 1 starter set,
Greenhouse-only) plus 10 curator-picked multi-provider examples covering
Lever- and Ashby-hosted boards. Curator extends the set in follow-up
YAML-only PRs; the spec calls for ~50 entries at v1 ship.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task B2: `seed_catalog(session)` function

**Files:**
- Modify: `app/services/company_catalog.py` — add `seed_catalog(session)` and supporting helpers.
- Create: `tests/integration/_catalog_fixtures/two_rows.yaml`
- Create: `tests/integration/_catalog_fixtures/empty.yaml`
- Create: `tests/integration/test_company_catalog_seed.py`

The seeder is what actually mutates the DB. Tested against a real Postgres (testcontainers) since the upsert + ON CONFLICT semantics matter.

- [ ] **Step 1: Create the test fixtures**

Create `tests/integration/_catalog_fixtures/two_rows.yaml`:

```yaml
companies:
  - canonical_name: TestStripe
    providers:
      greenhouse: teststripe
  - canonical_name: TestLinear
    providers:
      ashby: testlinear
```

Create `tests/integration/_catalog_fixtures/empty.yaml`:

```yaml
companies: []
```

(Test fixtures use prefixes like `TestStripe` / `teststripe` so they don't collide with the production catalog if both were ever loaded into the same DB — though tests use isolated testcontainers so the precaution is belt-and-suspenders.)

- [ ] **Step 2: Write the failing tests**

Create `tests/integration/test_company_catalog_seed.py`:

```python
"""Integration tests for company_catalog.seed_catalog."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import select

from app.models.company import Company
from app.services.company_catalog import seed_catalog


FIXTURES = Path(__file__).parent / "_catalog_fixtures"


@pytest.mark.asyncio
async def test_seed_catalog_inserts_yaml_rows_as_curated(db_session):
    """First boot on an empty DB: every YAML row materializes as is_curated=true."""
    yaml_path = FIXTURES / "two_rows.yaml"
    count = await seed_catalog(db_session, source=yaml_path)
    assert count == 2

    rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(rows) == 2
    by_key = {r.normalized_key: r for r in rows}
    assert by_key["teststripe"].canonical_name == "TestStripe"
    assert by_key["teststripe"].provider_slugs == {"greenhouse": "teststripe"}
    assert by_key["teststripe"].is_curated is True
    assert by_key["testlinear"].canonical_name == "TestLinear"
    assert by_key["testlinear"].provider_slugs == {"ashby": "testlinear"}
    assert by_key["testlinear"].is_curated is True


@pytest.mark.asyncio
async def test_seed_catalog_is_idempotent(db_session):
    """Running the seed twice produces the same DB state — no duplicate rows,
    no flag flapping, and the Company id is stable across runs."""
    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    first_ids = {
        r.normalized_key: r.id
        for r in (
            await db_session.execute(
                select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
            )
        ).scalars().all()
    }

    await seed_catalog(db_session, source=yaml_path)

    second_rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(second_rows) == 2
    second_ids = {r.normalized_key: r.id for r in second_rows}
    assert first_ids == second_ids
    assert all(r.is_curated for r in second_rows)


@pytest.mark.asyncio
async def test_seed_catalog_promotes_organic_company_to_curated(db_session):
    """A Company resolved organically via Layer 1 fan-out exists with
    is_curated=false. When the catalog adds a row that matches by
    normalized_key, the existing Company is upgraded — same id, is_curated
    flipped to true, provider_slugs refreshed from the YAML."""
    organic = Company(
        canonical_name="teststripe",  # lowercase casing from the user's input
        normalized_key="teststripe",
        provider_slugs={"greenhouse": "old-slug"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(organic)
    await db_session.commit()
    await db_session.refresh(organic)
    organic_id = organic.id
    assert organic.is_curated is False

    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    refreshed = (
        await db_session.execute(
            select(Company).where(Company.normalized_key == "teststripe")
        )
    ).scalar_one()
    assert refreshed.id == organic_id  # stable across promotion
    assert refreshed.is_curated is True
    assert refreshed.canonical_name == "TestStripe"  # YAML casing wins
    assert refreshed.provider_slugs == {"greenhouse": "teststripe"}  # YAML slugs win


@pytest.mark.asyncio
async def test_seed_catalog_drops_curated_flag_when_row_removed_from_yaml(db_session):
    """A row dropped from the YAML on a later deploy → is_curated flips to
    false on the next seed. The Company row stays in the DB; following users
    are unaffected."""
    yaml_path = FIXTURES / "two_rows.yaml"
    await seed_catalog(db_session, source=yaml_path)

    # Now seed against an empty catalog. The two rows from the previous run
    # should lose is_curated but stay in the DB.
    await seed_catalog(db_session, source=FIXTURES / "empty.yaml")

    rows = (
        await db_session.execute(
            select(Company).where(Company.normalized_key.in_(["teststripe", "testlinear"]))
        )
    ).scalars().all()
    assert len(rows) == 2  # not deleted
    assert all(r.is_curated is False for r in rows)


@pytest.mark.asyncio
async def test_seed_catalog_raises_on_malformed_file(db_session, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies:\n  - canonical_name: NoProvider\n    providers: {}\n")
    with pytest.raises(ValueError, match="no provider slugs"):
        await seed_catalog(db_session, source=bad)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_company_catalog_seed.py -v`
Expected: FAIL — `seed_catalog` doesn't exist yet.

- [ ] **Step 4: Implement `seed_catalog` in `app/services/company_catalog.py`**

Append to the bottom of `app/services/company_catalog.py` (the parser code from Task A2 stays at the top, unchanged):

```python
import structlog
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company

log = structlog.get_logger()

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog" / "companies.yaml"


async def seed_catalog(
    session: AsyncSession,
    *,
    source: Path = DEFAULT_CATALOG_PATH,
) -> int:
    """Seed the curated catalog into the companies table.

    Idempotent — every call:
      1. Resets is_curated=false on every row (via UPDATE).
      2. Upserts each YAML row, setting is_curated=true. Existing rows
         (matched by normalized_key) get their canonical_name + provider_slugs
         refreshed and the curated flag set; their id is preserved.

    Returns the number of YAML rows that were upserted (NOT the row count
    in the DB after the run).

    Raises ValueError on parser errors (malformed YAML, duplicates,
    rows with no providers). The caller should let that propagate so app
    startup fails loudly rather than silently shipping a broken catalog.
    """
    raw = Path(source).read_text()
    catalog = parse_catalog(raw)

    # Reset stale curated flags first.
    await session.execute(update(Company).values(is_curated=False))

    if not catalog.companies:
        await session.commit()
        await log.ainfo("catalog.seeded", count=0, source=str(source))
        return 0

    now = datetime.now(UTC)
    for row in catalog.companies:
        stmt = (
            insert(Company)
            .values(
                canonical_name=row.canonical_name,
                normalized_key=row.normalized_key,
                provider_slugs=row.provider_slugs_dict,
                is_curated=True,
                resolved_at=now,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["normalized_key"],
                set_={
                    "canonical_name": row.canonical_name,
                    "provider_slugs": row.provider_slugs_dict,
                    "is_curated": True,
                },
            )
        )
        await session.execute(stmt)

    await session.commit()
    await log.ainfo("catalog.seeded", count=len(catalog.companies), source=str(source))
    return len(catalog.companies)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_company_catalog_seed.py -v`
Expected: PASS — all 5 tests pass.

- [ ] **Step 6: Run the broader unit + integration suite as a regression check**

Run: `uv run pytest tests/unit/ tests/integration/ -q`
Expected: PASS. The seed reset (`UPDATE companies SET is_curated = false`) hits every row and could in theory race with concurrent inserts; the testcontainers session fixture is single-threaded per test so this is fine.

- [ ] **Step 7: Commit**

```bash
git add app/services/company_catalog.py tests/integration/_catalog_fixtures/ tests/integration/test_company_catalog_seed.py
git commit -m "feat(catalog): seed_catalog idempotent boot-time seeder

Single-transaction reset-then-upsert: UPDATE companies SET is_curated=false,
then INSERT ... ON CONFLICT (normalized_key) DO UPDATE for each YAML row,
flipping is_curated=true. Pre-existing organic rows (resolved via Layer 1
fan-out) are upgraded to curated status; their id stays stable so any user
already following them keeps the link. Rows dropped from the YAML lose
is_curated but stay in the DB — following users are unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task B3: Wire `seed_catalog` into FastAPI lifespan

**Files:**
- Modify: `app/main.py` — call `seed_catalog` inside `lifespan` after the checkpointer setup, before `yield`.

- [ ] **Step 1: Read the existing lifespan handler**

Open `app/main.py` and locate the `lifespan` async context manager (starts around line 71). Note the structure:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    log = structlog.get_logger()
    ...
    if settings.environment == "development":
        await init_db()
    ...
    async with AsyncConnectionPool(...) as pool:
        app.state.checkpointer = AsyncPostgresSaver(pool)
        await log.ainfo("checkpointer.ready")
        yield   # <-- seed must run BEFORE this line
    await log.ainfo("app.shutdown")
```

We add the seed call inside the `async with AsyncConnectionPool(...)` block, immediately before `yield`. Using a separate session — the checkpointer's psycopg pool is not the SQLAlchemy session factory.

- [ ] **Step 2: Add the seed invocation**

In `app/main.py::lifespan`, immediately before the `yield` line:

```python
        # Seed the curated catalog on every boot. Idempotent — pre-Layer-2
        # rows just have is_curated=false flipped to true (or vice versa
        # for rows dropped from the YAML).
        from app.database import get_session_factory
        from app.services.company_catalog import seed_catalog

        async with get_session_factory()() as session:
            count = await seed_catalog(session)
            await log.ainfo("catalog.ready", count=count)

        yield
```

The imports stay inside the function (lazy) to avoid module-import cycles between `app.main`, `app.database`, and `app.services.company_catalog`. This mirrors the pattern other lifespan-internal imports use.

- [ ] **Step 3: Manually verify the wiring**

Stop any running uvicorn instance, then:

```bash
DATABASE_URL='postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent' \
GOOGLE_API_KEY='fake-test-key' \
ENVIRONMENT='development' \
uv run uvicorn app.main:app --port 8000 &
sleep 5
curl -s http://localhost:8000/health
kill %1 2>/dev/null
```

Expected: the server starts, the structlog output includes a `catalog.seeded count=25` (or similar) line and a `catalog.ready count=25` line, then `/health` returns 200. After confirming, kill the background server.

- [ ] **Step 4: Verify the catalog rows landed**

```bash
psql -h localhost -U jobagent -d jobagent -c "SELECT canonical_name FROM companies WHERE is_curated = true ORDER BY canonical_name LIMIT 30"
```

Expected: a list of the 25 catalog entries (Airbnb, Anthropic, Asana, Brex, Cloudflare, Datadog, DoorDash, Dropbox, Figma, GitHub, Gusto, Instacart, Linear, Mercury, Modal, Notion, OpenAI, Posthog, Ramp, Replicate, Robinhood, Samsara, Scale AI, Stripe, Vercel) — alphabetical.

- [ ] **Step 5: Run the full test suite as a regression check**

Run: `uv run pytest tests/unit/ tests/integration/ -q`
Expected: PASS. The lifespan-wired seed runs on every test that boots the FastAPI app via `ASGITransport`; integration tests using a fresh testcontainers DB will see 25 catalog rows in their session — verify no tests break on the unexpected row count.

If a test breaks because it counts `Company` rows globally (e.g. `assert len(all_companies) == 0` on a fresh test DB), it needs to be tightened to filter by the test's seeded fixture data. Update inline.

- [ ] **Step 6: Commit**

```bash
git add app/main.py
git commit -m "feat(catalog): wire seed_catalog into FastAPI lifespan

Idempotent seed runs after the checkpointer pool opens, before the app
starts handling requests. Cold-start cost: ~25 upserts in a single
transaction — well under the 30s lifespan budget.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Track C — API + cleanup

### Task C1: `GET /api/companies/catalog` endpoint

**Files:**
- Modify: `app/api/companies.py` — add the new endpoint alongside the existing `POST /api/companies/resolve`.
- Test: `tests/integration/test_companies_catalog_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_companies_catalog_api.py`:

```python
"""Integration tests for GET /api/companies/catalog."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.models.company import Company
from app.services.company_catalog import seed_catalog


FIXTURES = Path(__file__).parent / "_catalog_fixtures"


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_only_curated_rows(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    # Seed two curated rows.
    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    # Add an organic row that should NOT appear.
    organic = Company(
        canonical_name="OrganicCo",
        normalized_key="organicco",
        provider_slugs={"greenhouse": "organicco"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(organic)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    names = {row["canonical_name"] for row in body}
    assert "TestStripe" in names
    assert "TestLinear" in names
    assert "OrganicCo" not in names


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_alphabetical_order(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    body = resp.json()
    names = [row["canonical_name"] for row in body]
    # Case-insensitive alphabetical: TestLinear < TestStripe.
    assert names == sorted(names, key=lambda s: s.lower())


@pytest.mark.asyncio
async def test_catalog_endpoint_response_shape(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    await seed_catalog(db_session, source=FIXTURES / "two_rows.yaml")

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog", headers=auth_headers)

    body = resp.json()
    assert isinstance(body, list)
    for row in body:
        assert set(row.keys()) == {"id", "canonical_name"}
        assert isinstance(row["id"], str)
        assert isinstance(row["canonical_name"], str)


@pytest.mark.asyncio
async def test_catalog_endpoint_requires_auth(db_session):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/companies/catalog")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_companies_catalog_api.py -v`
Expected: FAIL — endpoint returns 404 (not registered yet).

- [ ] **Step 3: Add the endpoint to `app/api/companies.py`**

Locate the existing `POST /api/companies/resolve` handler. Append the new GET endpoint alongside it. The full file should now look like:

```python
"""Company resolution + catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.company import Company
from app.models.user_profile import UserProfile
from app.services import company_resolver

router = APIRouter(prefix="/api/companies", tags=["companies"])


class ResolveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/resolve")
async def resolve_company(
    body: ResolveRequest,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Resolve a free-text company name to a Company row via fan-out across
    every supported ATS provider.

    Returns:
      200 — confirmed match
      400 — empty/whitespace name
      404 — every provider returned 404 (confirmed miss)
      503 — fan-out timed out (transient; user retries)
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        company = await company_resolver.resolve(body.name, session)
    except company_resolver.FanoutTimeoutError:
        raise HTTPException(status_code=503, detail="couldn't reach our boards right now")
    if company is None:
        raise HTTPException(status_code=404, detail="company not found on any supported board")
    return {
        "company": {
            "id": str(company.id),
            "canonical_name": company.canonical_name,
            "providers": list(company.provider_slugs.keys()),
        }
    }


@router.get("/catalog")
async def get_catalog(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Return the full curated company catalog, alphabetical by canonical_name.

    ~50 rows post-curation; sub-1KB JSON. Auth-gated for consistency with
    the rest of /api/companies, even though the data is identical for every
    caller (no per-user filtering).
    """
    rows = (
        await session.execute(
            select(Company.id, Company.canonical_name)
            .where(Company.is_curated.is_(True))
            .order_by(func.lower(Company.canonical_name))
        )
    ).all()
    return [{"id": str(r.id), "canonical_name": r.canonical_name} for r in rows]
```

(The existing `resolve_company` handler is unchanged — only the new `get_catalog` is appended, plus the imports it needs: `func`, `select`, `Company`. If the file already imports any of those, leave them; just confirm via the final state.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_companies_catalog_api.py -v`
Expected: PASS — all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/api/companies.py tests/integration/test_companies_catalog_api.py
git commit -m "feat(catalog): GET /api/companies/catalog endpoint

Returns is_curated=true rows ordered case-insensitive by canonical_name.
~50 rows / ~3KB JSON / auth-gated. The frontend loads this once via
TanStack Query (staleTime: Infinity) and filters in-memory.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task C2: Delete `default_slugs.py`, `seed_defaults_if_empty`, and the live test

**Files:**
- Delete: `app/data/default_slugs.py`
- Delete: `tests/integration/test_default_slugs_live.py` (replaced by `test_catalog_live.py` in Track E)
- Modify: `app/services/profile_service.py` — drop the `seed_defaults_if_empty` no-op.
- Modify: any caller of `seed_defaults_if_empty`.

The Layer 1 D4 task already turned `seed_defaults_if_empty` into a no-op. Layer 2 deletes it now that the curated catalog supersedes the old default-slug machinery.

- [ ] **Step 1: Find all callers**

Run: `rg -n 'seed_defaults_if_empty|default_slugs|DEFAULT_SLUGS' app/ tests/`

Expected hits:
- `app/services/profile_service.py` — definition.
- `app/services/job_sync_service.py` — caller in `prune_and_enqueue` (Layer 1 D4 left this calling the no-op).
- `app/data/default_slugs.py` — to delete.
- `tests/integration/test_default_slugs_live.py` — to delete.
- `tests/unit/test_default_slugs_catalog.py` (if present from a Layer 1 era) — investigate; if it's testing the constant `DEFAULT_SLUGS`, the test goes away. If it's testing parser-shaped behavior that's now subsumed by the catalog parser, the test goes away.

- [ ] **Step 2: Delete the files**

```bash
git rm app/data/default_slugs.py
git rm tests/integration/test_default_slugs_live.py
# Only if the file exists and was testing DEFAULT_SLUGS shape:
git rm tests/unit/test_default_slugs_catalog.py
```

- [ ] **Step 3: Drop `seed_defaults_if_empty` from `profile_service.py`**

Open `app/services/profile_service.py`. Delete the `seed_defaults_if_empty` function entirely (currently a no-op returning False). Save.

- [ ] **Step 4: Drop the caller in `job_sync_service.py`**

In `app/services/job_sync_service.py::prune_and_enqueue`, find the `seeded = seed_defaults_if_empty(profile)` line and the surrounding `if seeded:` block. Replace with:

```python
async def prune_and_enqueue(profile: UserProfile, session: AsyncSession) -> dict:
    """Cron-safe profile sync: prune invalid (provider, slug) pairs from
    followed Companies + enqueue stale + update last_sync_*. Returns the
    same summary shape as `sync_profile` with `matched_now=0`.

    seeded_defaults is no longer populated (catalog supersedes the
    automatic-seed-on-empty path); it remains in the summary dict at False
    for backward-compat with any callers that still read the key.
    """
    pruned = await _prune_invalid_provider_slugs(profile, session)
    if pruned:
        await session.commit()

    queued = await slug_registry_service.enqueue_stale(profile, session, ttl_hours=6)
    summary = {
        "queued_slugs": queued,
        "matched_now": 0,
        "seeded_defaults": False,
        "pruned_slugs": pruned,
    }
    profile.last_sync_requested_at = datetime.now(UTC)
    profile.last_sync_summary = summary
    if not queued:
        profile.last_sync_completed_at = datetime.now(UTC)
    session.add(profile)
    await session.commit()
    return summary
```

(Remove the `from app.services.profile_service import seed_defaults_if_empty` import too.)

- [ ] **Step 5: Run the test suite**

Run: `uv run pytest tests/unit/ tests/integration/ -q`

Expected: PASS. Any test that asserted `seeded_defaults=True` after `prune_and_enqueue` was already updated to expect `False` in Layer 1 D4. Any test that imports `seed_defaults_if_empty` or `DEFAULT_SLUGS` will fail — fix by removing the import + assertion (the symbols are gone).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(catalog): drop default_slugs.py and seed_defaults_if_empty

Both became dead surface once Layer 2's curated catalog landed:
  - app/data/default_slugs.py: superseded by app/data/catalog/companies.yaml.
  - profile_service.seed_defaults_if_empty: already a no-op since Layer 1 D4;
    job_sync_service.prune_and_enqueue stops calling it and pins the summary
    field to seeded_defaults=False for backward-compat.
  - tests/integration/test_default_slugs_live.py: replaced by
    tests/integration/test_catalog_live.py (Track E).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Track D — Frontend typeahead

### Task D1: API client helper `getCompanyCatalog()`

**Files:**
- Modify: `frontend/src/api/client.ts` — add the helper.

- [ ] **Step 1: Add the helper**

In `frontend/src/api/client.ts`, find the `api` object literal (or wherever `resolveCompany`, `updateProfile` etc. are defined). Append:

```ts
async getCompanyCatalog(): Promise<{ id: string; canonical_name: string }[]> {
  const resp = await fetch('/api/companies/catalog', {
    method: 'GET',
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  return resp.json()
},
```

(Match the file's existing comma-trailing / brace style — read the file first to confirm.)

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: clean (no output).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(frontend): api.getCompanyCatalog helper

Wraps GET /api/companies/catalog. Returns the full curated list as
[{id, canonical_name}]; ~3KB JSON. The Settings typeahead loads it once
with staleTime: Infinity since the catalog only changes on deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task D2: Typeahead dropdown in `FollowedCompaniesSection.tsx`

**Files:**
- Modify: `frontend/src/components/settings/FollowedCompaniesSection.tsx`
- Modify: `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`

This is the user-visible work. The dropdown layers over the existing input; everything else (chips, optimistic add, rollback, error states) stays as-is.

- [ ] **Step 1: Write failing component tests**

In `frontend/src/components/settings/FollowedCompaniesSection.test.tsx`, append the following block (don't replace the existing tests — those still exercise the chip + 404 + rollback behavior). Add the catalog mock at the top of the file alongside the existing `vi.mock('../../api/client', ...)` block:

```tsx
// Update the existing vi.mock block at the top of the file to include
// getCompanyCatalog. The original mock looks like:
//   vi.mock('../../api/client', () => ({
//     api: {
//       resolveCompany: vi.fn(),
//       updateProfile: vi.fn(),
//     },
//   }))
// Replace with:
vi.mock('../../api/client', () => ({
  api: {
    resolveCompany: vi.fn(),
    updateProfile: vi.fn(),
    getCompanyCatalog: vi.fn().mockResolvedValue([
      { id: 'cat-1', canonical_name: 'Anthropic' },
      { id: 'cat-2', canonical_name: 'Linear' },
      { id: 'cat-3', canonical_name: 'Stripe' },
    ]),
  },
}))
```

(If the existing mock is structured differently — e.g. via `__mocks__` — match the file's convention.)

Append these tests to the existing `describe('FollowedCompaniesSection', ...)` block:

```tsx
it('opens the typeahead dropdown when the user types', async () => {
  render(withQuery(<FollowedCompaniesSection companies={[]} />))
  const input = screen.getByPlaceholderText(/Add a company/i)

  await userEvent.type(input, 'lin')

  expect(await screen.findByText('Linear')).toBeInTheDocument()
  // Anthropic and Stripe don't match "lin" — should NOT appear in dropdown.
  expect(screen.queryByText('Anthropic')).not.toBeInTheDocument()
  expect(screen.queryByText('Stripe')).not.toBeInTheDocument()
})

it('selecting a dropdown row via Enter resolves and adds the chip', async () => {
  ;(api.resolveCompany as any).mockResolvedValue({
    id: 'cat-3',
    canonical_name: 'Stripe',
    providers: ['greenhouse'],
  })
  ;(api.updateProfile as any).mockResolvedValue({ id: 'p', updated: true })

  render(withQuery(<FollowedCompaniesSection companies={[]} />))
  const input = screen.getByPlaceholderText(/Add a company/i)
  await userEvent.type(input, 'str')

  // First (and only) match should be highlighted on first ↓; Enter selects.
  await screen.findByText('Stripe')
  await userEvent.keyboard('{ArrowDown}')
  await userEvent.keyboard('{Enter}')

  await waitFor(() => expect(screen.getAllByText('Stripe')).toHaveLength(1))
  expect(api.resolveCompany).toHaveBeenCalledWith('Stripe')
})

it('clicking a dropdown row resolves and adds the chip', async () => {
  ;(api.resolveCompany as any).mockResolvedValue({
    id: 'cat-2',
    canonical_name: 'Linear',
    providers: ['ashby'],
  })
  ;(api.updateProfile as any).mockResolvedValue({ id: 'p', updated: true })

  render(withQuery(<FollowedCompaniesSection companies={[]} />))
  const input = screen.getByPlaceholderText(/Add a company/i)
  await userEvent.type(input, 'lin')

  const row = await screen.findByRole('option', { name: 'Linear' })
  await userEvent.click(row)

  expect(api.resolveCompany).toHaveBeenCalledWith('Linear')
})

it('Enter with no matches falls through to the existing resolve flow', async () => {
  ;(api.resolveCompany as any).mockRejectedValue(
    new Error("Couldn't find that company on any of our supported boards.")
  )

  render(withQuery(<FollowedCompaniesSection companies={[]} />))
  const input = screen.getByPlaceholderText(/Add a company/i)
  await userEvent.type(input, 'totally-fake-co{Enter}')

  // The dropdown shows the no-match copy.
  expect(await screen.findByText(/No matches/i)).toBeInTheDocument()
  // resolveCompany was called with the literal draft (not a catalog name).
  expect(api.resolveCompany).toHaveBeenCalledWith('totally-fake-co')
})

it('already-followed companies are filtered out of the dropdown', async () => {
  render(withQuery(
    <FollowedCompaniesSection companies={[
      { id: 'cat-1', canonical_name: 'Anthropic' },
    ]} />
  ))
  const input = screen.getByPlaceholderText(/Add a company/i)
  await userEvent.type(input, 'a')

  // 'Linear' matches 'a'; 'Anthropic' does too but is filtered out.
  expect(await screen.findByText('Linear')).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: 'Anthropic' })).not.toBeInTheDocument()
})

it('Esc closes the dropdown without selecting', async () => {
  render(withQuery(<FollowedCompaniesSection companies={[]} />))
  const input = screen.getByPlaceholderText(/Add a company/i)
  await userEvent.type(input, 'lin')

  await screen.findByText('Linear')
  await userEvent.keyboard('{Escape}')

  await waitFor(() => expect(screen.queryByRole('option', { name: 'Linear' })).not.toBeInTheDocument())
  // Draft text stays in the input.
  expect(input).toHaveValue('lin')
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend && npm test -- FollowedCompaniesSection
```

Expected: the new typeahead tests FAIL — the dropdown doesn't exist yet. Existing chip / 404 / rollback tests should still PASS.

- [ ] **Step 3: Update the component**

Open `frontend/src/components/settings/FollowedCompaniesSection.tsx` and refactor to add the typeahead. The full new file:

```tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface Company {
  id: string
  canonical_name: string
}

export interface FollowedCompaniesSectionProps {
  companies: Company[]
}

const MAX_DROPDOWN_ROWS = 8

export function FollowedCompaniesSection({ companies }: FollowedCompaniesSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [optimistic, setOptimistic] = useState<Company[]>(companies)
  const [busy, setBusy] = useState(false)
  const [highlight, setHighlight] = useState<number>(-1)
  const [open, setOpen] = useState(false)
  const prevCompaniesRef = useRef(companies)

  // Sync optimistic with prop changes (parent refetched profile).
  useEffect(() => {
    if (prevCompaniesRef.current !== companies && !busy) {
      setOptimistic(companies)
      prevCompaniesRef.current = companies
    }
  }, [companies, busy])

  const { data: catalog = [] } = useQuery({
    queryKey: ['companies', 'catalog'],
    queryFn: api.getCompanyCatalog,
    staleTime: Infinity,
  })

  const followedIds = useMemo(() => new Set(optimistic.map(c => c.id)), [optimistic])

  const matches = useMemo(() => {
    const q = draft.trim().toLowerCase()
    if (!q) return [] as Company[]
    return catalog
      .filter(c => !followedIds.has(c.id))
      .filter(c => c.canonical_name.toLowerCase().includes(q))
      .slice(0, MAX_DROPDOWN_ROWS)
  }, [draft, catalog, followedIds])

  // Reset highlight whenever the match set changes.
  useEffect(() => { setHighlight(matches.length > 0 ? -1 : -1) }, [matches])

  const patch = useMutation({
    mutationFn: (ids: string[]) => api.updateProfile({ target_company_ids: ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
  })

  async function commit(name: string) {
    setError(null)
    setBusy(true)
    let resolved: { id: string; canonical_name: string } | null = null
    try {
      resolved = await api.resolveCompany(name)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
      return
    }
    const next = [...optimistic, { id: resolved.id, canonical_name: resolved.canonical_name }]
    setOptimistic(next)
    setDraft('')
    setOpen(false)
    track('settings.company_added', { company_id: resolved.id, canonical_name: resolved.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)
      show((e as Error)?.message ?? 'Could not save', 'error')
    } finally {
      setBusy(false)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (matches.length === 0) return
      setHighlight(h => Math.min(h + 1, matches.length - 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (matches.length === 0) return
      setHighlight(h => Math.max(h - 1, 0))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (matches.length > 0 && highlight >= 0 && highlight < matches.length) {
        commit(matches[highlight].canonical_name)
      } else {
        const trimmed = draft.trim()
        if (trimmed) commit(trimmed)
      }
    }
  }

  async function remove(id: string) {
    const company = optimistic.find(c => c.id === id)
    const next = optimistic.filter(c => c.id !== id)
    setOptimistic(next)
    track('settings.company_removed', { company_id: id, canonical_name: company?.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)
      show((e as Error)?.message ?? 'Could not save', 'error')
    }
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Followed companies</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-3">
        <p className="text-sm text-subtle">We'll match you to roles posted by these companies.</p>
        <div className="flex flex-wrap gap-2">
          {optimistic.map(c => (
            <span
              key={c.id}
              className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text"
            >
              {c.canonical_name}
              <button
                type="button"
                aria-label={`Remove ${c.canonical_name}`}
                onClick={() => remove(c.id)}
                className="text-muted hover:text-danger"
              >×</button>
            </span>
          ))}
          {optimistic.length === 0 && (
            <p className="text-xs text-subtle">No companies followed yet.</p>
          )}
        </div>
        <div className="relative">
          <input
            type="text"
            value={draft}
            onChange={e => { setDraft(e.target.value); setOpen(e.target.value.trim().length > 0) }}
            onFocus={() => setOpen(draft.trim().length > 0)}
            onBlur={() => setTimeout(() => setOpen(false), 100)}
            onKeyDown={onKeyDown}
            placeholder="Add a company you want to follow"
            disabled={busy}
            className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
          />
          {open && (
            <div
              role="listbox"
              className="absolute left-0 right-0 mt-1 bg-surface border border-border rounded-md-token shadow-lg z-10"
            >
              {matches.length === 0 ? (
                <p className="px-2 py-1.5 text-xs text-subtle">No matches — press Enter to search the boards</p>
              ) : (
                matches.map((c, i) => (
                  <div
                    key={c.id}
                    role="option"
                    aria-selected={highlight === i}
                    aria-label={c.canonical_name}
                    onMouseDown={(e) => { e.preventDefault(); commit(c.canonical_name) }}
                    onMouseEnter={() => setHighlight(i)}
                    className={`px-2 py-1.5 text-sm cursor-pointer ${highlight === i ? 'bg-surface-2' : ''}`}
                  >
                    {c.canonical_name}
                  </div>
                ))
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="text-xs text-danger mt-1">{error}</p>
          )}
        </div>
      </div>
    </section>
  )
}
```

Key behaviors:
- `onMouseDown` (not `onClick`) because `onBlur` would fire first and close the dropdown before the click registers. `e.preventDefault()` in mousedown keeps the input focused.
- `onBlur` has a 100ms timeout so a click on a dropdown row has time to fire.
- `useEffect` on `companies` syncs prop changes safely without the React 18 setState-during-render footgun the Layer 1 reviewer flagged.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend && npm test -- FollowedCompaniesSection
```

Expected: PASS — both the existing tests and the new typeahead tests.

- [ ] **Step 5: TypeScript clean**

```bash
cd frontend && npx tsc --noEmit
```

Expected: clean (no output).

- [ ] **Step 6: Manually verify visually**

In one shell:
```bash
DATABASE_URL='postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent' \
GOOGLE_API_KEY='fake-test-key' \
ENVIRONMENT='development' \
uv run uvicorn app.main:app --port 8000
```

In another:
```bash
cd frontend && npm run dev
```

Open `http://localhost:5173/settings`. Type `lin` — dropdown shows `Linear`. Click or Enter — chip appears. Type `gobbledygook` — dropdown shows `No matches — press Enter to search the boards`; Enter triggers fan-out and the inline 404 error. Capture a screenshot of the open dropdown for the PR.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/settings/FollowedCompaniesSection.tsx frontend/src/components/settings/FollowedCompaniesSection.test.tsx
git commit -m "feat(catalog): typeahead dropdown in FollowedCompaniesSection

Loads the curated catalog once via TanStack Query (staleTime: Infinity).
Substring-match against canonical_name, top 8 results, already-followed
filtered out by id. ↓/↑ keyboard nav, Enter selects highlighted row, Esc
closes. No-match path shows 'press Enter to search the boards' and falls
through to the existing Layer 1 fan-out.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Track E — Live validation

### Task E1: `--catalog-live` flag + `test_catalog_live.py`

**Files:**
- Modify: `tests/conftest.py` — add the `--catalog-live` flag.
- Create: `tests/integration/test_catalog_live.py`

This is a real-network test. Behind a flag so it doesn't run on PR CI.

- [ ] **Step 1: Register the pytest flag**

Open `tests/conftest.py`. Locate the existing `pytest_addoption` hook (the one that registers `--has-seed-api`). Append:

```python
    parser.addoption(
        "--catalog-live",
        action="store_true",
        default=False,
        help=(
            "Run the catalog-live validation tests against real public ATS boards. "
            "Used by the nightly validate-catalog GitHub Actions workflow; off by default "
            "for local + PR CI runs."
        ),
    )
```

In the same file, register the `catalog_live` mark so pytest doesn't warn about unknown marks:

```python
def pytest_configure(config):
    # ... any existing config hooks stay as-is ...
    config.addinivalue_line(
        "markers",
        "catalog_live: hits real public ATS boards; only run with --catalog-live.",
    )
```

(If `pytest_configure` doesn't already exist in the file, add it; otherwise append the `addinivalue_line` to the existing function.)

Add a `pytest_collection_modifyitems` hook so tests marked `catalog_live` are skipped when the flag isn't set:

```python
def pytest_collection_modifyitems(config, items):
    if config.getoption("--catalog-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --catalog-live to run")
    for item in items:
        if "catalog_live" in item.keywords:
            item.add_marker(skip_live)
```

(If the file already has a `pytest_collection_modifyitems`, append to its body.)

- [ ] **Step 2: Write the live validation test**

Create `tests/integration/test_catalog_live.py`:

```python
"""Live validation of the curated catalog against the real public ATS boards.

Marked `catalog_live`; only runs with --catalog-live. The nightly
validate-catalog GitHub Actions workflow invokes it; PR CI skips it.

A single test parametrized over every (provider, slug) pair in
companies.yaml. Each parametrized case fails independently so a single
broken entry doesn't mask the others.
"""

from pathlib import Path

import httpx
import pytest

from app.services.company_catalog import parse_catalog
from app.sources import SOURCES


CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "app" / "data" / "catalog" / "companies.yaml"


def _all_pairs():
    catalog = parse_catalog(CATALOG_PATH.read_text())
    return [
        (row.canonical_name, provider, slug)
        for row in catalog.companies
        for provider, slug in row.provider_slugs_dict.items()
    ]


@pytest.mark.catalog_live
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "canonical_name,provider,slug",
    _all_pairs(),
    ids=lambda v: str(v),
)
async def test_catalog_entry_resolves(canonical_name: str, provider: str, slug: str):
    """Each (provider, slug) in the catalog must validate against the real
    public board. A True return = the board exists. False = 404; raise = transient."""
    adapter = SOURCES[provider]
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        ok = await adapter.validate(slug, client=client)
    assert ok, f"{canonical_name!r}: {provider}={slug!r} returned False (board missing?)"
```

- [ ] **Step 3: Verify the test is registered + skipped without the flag**

```bash
uv run pytest tests/integration/test_catalog_live.py --co -q
```

Expected: collection lists ~25 parametrized cases (one per (provider, slug) pair).

```bash
uv run pytest tests/integration/test_catalog_live.py -v
```

Expected: every parametrized case skipped with reason `needs --catalog-live to run`.

- [ ] **Step 4: Run with the flag once locally**

```bash
uv run pytest tests/integration/test_catalog_live.py --catalog-live -v
```

Expected: all 25 (or however many entries are in the YAML) pass against the real Greenhouse / Lever / Ashby public boards.

If a case fails, that's a real catalog problem — fix the YAML before committing.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/integration/test_catalog_live.py
git commit -m "test(catalog): live validation behind --catalog-live flag

Parametrized test exercising every (provider, slug) in companies.yaml
against the real public board. Marked catalog_live; PR CI skips, nightly
validate-catalog cron runs with the flag set. A single broken entry fails
its own parametrized case so the curator can pinpoint which row to fix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task E2: `validate-catalog.yml` GitHub Actions workflow

**Files:**
- Create: `.github/workflows/validate-catalog.yml`

- [ ] **Step 1: Inspect existing workflows for the project's conventions**

```bash
ls .github/workflows/
cat .github/workflows/cron.yml 2>/dev/null | head -40
```

Note the patterns: `actions/checkout`, `setup-python` (or whatever the project uses for `uv`), the `uv sync --dev` step, env-var sourcing for secrets, the issue-creation pattern (if `cron.yml` already opens issues on failure, mimic).

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/validate-catalog.yml`:

```yaml
name: validate-catalog

on:
  schedule:
    - cron: "0 7 * * *"  # 07:00 UTC daily
  workflow_dispatch: {}

jobs:
  validate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync --dev
      - name: Run live catalog validation
        id: pytest
        run: |
          uv run pytest tests/integration/test_catalog_live.py \
            --catalog-live -v \
            --tb=short \
            --junit-xml=catalog-results.xml \
          | tee catalog.log
      - name: Upload pytest results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: catalog-validation
          path: |
            catalog-results.xml
            catalog.log
      - name: Open or update tracking issue on failure
        if: failure()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs')
            const log = fs.readFileSync('catalog.log', 'utf-8').slice(-3000)
            const title = 'catalog: live validation failed'
            const body = `Nightly catalog validation failed. Tail of pytest output:\n\n\`\`\`\n${log}\n\`\`\`\n\nWorkflow run: ${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`

            const issues = await github.rest.issues.listForRepo({
              owner: context.repo.owner,
              repo: context.repo.repo,
              state: 'open',
              labels: ['catalog-validation'],
              per_page: 20,
            })
            const existing = issues.data.find(i => i.title === title)
            if (existing) {
              await github.rest.issues.createComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: existing.number,
                body,
              })
            } else {
              await github.rest.issues.create({
                owner: context.repo.owner,
                repo: context.repo.repo,
                title,
                body,
                labels: ['catalog-validation'],
              })
            }
```

The `actions/setup-python`/`astral-sh/setup-uv` step should match what other workflows in the repo use. If the project uses a different installer (e.g. `pip install -e .` or a custom action), copy from the existing `cron.yml` or `ci.yml` at the same step.

The issue-on-failure script is idempotent: if an open issue tagged `catalog-validation` with the matching title exists, it appends a comment instead of creating a duplicate. The curator closes the issue manually after fixing the YAML.

- [ ] **Step 3: Verify YAML syntax**

```bash
yq . .github/workflows/validate-catalog.yml > /dev/null
```

Expected: no error. (If `yq` isn't installed, use `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/validate-catalog.yml'))"`.)

- [ ] **Step 4: Trigger the workflow manually once it's on `main`**

After this PR merges, run:

```bash
gh workflow run validate-catalog.yml
gh run watch
```

Expected: workflow completes; if all entries are healthy, the `validate` job passes and no issue is opened.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/validate-catalog.yml
git commit -m "ci(catalog): nightly validate-catalog workflow

Cron 07:00 UTC daily. Runs the catalog-live test against the real public
boards and opens (or comments on) a tracking issue tagged
'catalog-validation' on failure. Idempotent: one open issue per failure
state, comments append on subsequent failed runs until the curator closes
it post-fix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Track F — Final verification

### Task F1: Full suite + manual UI check + screenshots

- [ ] **Step 1: Run full backend suite**

```bash
uv run pytest tests/unit/ tests/integration/ -q
```

Expected: PASS, with 25+ new tests added by this plan (parser, seed, catalog API, live validation skipped without flag).

- [ ] **Step 2: Run full frontend suite + typecheck**

```bash
cd frontend && npm test && npx tsc --noEmit
```

Expected: PASS.

- [ ] **Step 3: Verify migration applies cleanly on a fresh DB**

```bash
make migrate ARGS="downgrade -1"   # roll back the is_curated migration
make migrate ARGS="upgrade head"   # re-apply
```

Expected: both succeed without errors. The catalog table content survives down/up because the column add is reversible without data loss.

- [ ] **Step 4: Manually exercise the typeahead**

Run backend + frontend per Task D2 step 6. Capture three screenshots for the PR:
- Dropdown open with substring matches.
- Empty / no-match state showing the "press Enter to search the boards" copy.
- A chip added via dropdown selection.

Save them under `docs/superpowers/screenshots/2026-05-08-curated-company-catalog/` with descriptive filenames (e.g. `01-dropdown-open.png`, `02-no-match.png`, `03-chip-added.png`).

- [ ] **Step 5: Commit screenshots**

```bash
git add docs/superpowers/screenshots/2026-05-08-curated-company-catalog/
git commit -m "docs(catalog): screenshots for the typeahead dropdown

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Final clean-commit pass**

```bash
git log --oneline origin/main..HEAD
git status
```

Expected: a clean ladder of commits (one per task), no uncommitted changes. Ready to push and open the PR.

---

## Out-of-scope (intentional deferrals — separate PRs)

- Layer 3 chat-driven semantic matching ("companies that hire similar profiles") — needs metadata on the catalog rows, not in scope here.
- Per-company metadata fields (industry tag, size bucket, HQ region) — minimal-metadata decision in the spec.
- Admin UI for catalog curation — curation stays YAML-PR-based.
- Auto-promotion of organic Company rows to `is_curated=true` based on follower count.
- A "request company" form for users when off-list resolution fails repeatedly.
- Substring scoring / fuzzy matching beyond plain case-insensitive contains.
- Catalog growth past the 25-entry starter set — handled by the curator in YAML-only follow-up PRs after this plan ships.
