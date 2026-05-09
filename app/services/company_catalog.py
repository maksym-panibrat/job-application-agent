"""Catalog YAML parser + idempotent boot-time seeder.

The catalog source is hand-curated at app/data/catalog/companies.yaml.
Each row maps to one Company entity (one row per company across all the
ATSs it appears on). The parser enforces:
  - at least one provider slug per row
  - unique canonical_name across the file
  - unique normalized_key across the file (so two casings of the same name
    don't collide at INSERT time)

seed_catalog() runs on FastAPI startup: resets is_curated=false on every
existing companies row, then upserts each YAML row with is_curated=true.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.services.company_resolver import normalize

log = structlog.get_logger()

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "catalog" / "companies.yaml"
)


class CatalogProviderSlugs(BaseModel):
    greenhouse: str | None = None
    lever: str | None = None
    ashby: str | None = None


class CatalogRow(BaseModel):
    canonical_name: str
    providers: CatalogProviderSlugs
    tags: list[str] = Field(default_factory=list)

    @property
    def normalized_key(self) -> str:
        return normalize(self.canonical_name)

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
    def _has_at_least_one_provider(self) -> CatalogRow:
        if not self.provider_slugs_dict:
            raise ValueError(f"row {self.canonical_name!r} has no provider slugs")
        return self


class Catalog(BaseModel):
    companies: list[CatalogRow]

    @model_validator(mode="after")
    def _no_duplicates(self) -> Catalog:
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
                prior_idx = keys[row.normalized_key]
                prior_name = self.companies[prior_idx].canonical_name
                raise ValueError(
                    f"duplicate normalized_key {row.normalized_key!r} at "
                    f"rows {prior_idx} and {i} "
                    f"(canonical_names: {prior_name!r}, {row.canonical_name!r})"
                )
            keys[row.normalized_key] = i
        return self


def parse_catalog(raw: str) -> Catalog:
    """Parse YAML text -> Catalog. Raises ValueError on any structural,
    duplicate, or empty-providers error."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"catalog must be a mapping at the top level, got {type(data).__name__}")
    return Catalog.model_validate(data)


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

    Calls session.expire_all() after commit because the bulk UPDATE +
    ON CONFLICT DO UPDATE statements bypass the ORM identity map. Without
    expiry, any Company instance the caller had in this session before
    seed_catalog runs would still report its pre-seed attribute values on
    subsequent reads. Cost is zero on the boot-time call path (no other
    Company objects in that session); benefit is no footgun for future
    in-process callers.
    """
    raw = Path(source).read_text()
    catalog = parse_catalog(raw)

    # Reset stale curated flags first.
    await session.execute(update(Company).values(is_curated=False))

    if not catalog.companies:
        await session.commit()
        session.expire_all()
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
                tags=row.tags,
                is_curated=True,
                resolved_at=now,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["normalized_key"],
                set_={
                    "canonical_name": row.canonical_name,
                    "provider_slugs": row.provider_slugs_dict,
                    "tags": row.tags,
                    "is_curated": True,
                },
            )
        )
        await session.execute(stmt)

    await session.commit()
    session.expire_all()
    await log.ainfo("catalog.seeded", count=len(catalog.companies), source=str(source))
    return len(catalog.companies)
