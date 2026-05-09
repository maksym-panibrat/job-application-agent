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
