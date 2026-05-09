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

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "app" / "data" / "catalog" / "companies.yaml"
)


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
    # 30s — Ashby's public posting-api downloads the full board JSON on GET;
    # large boards (OpenAI ~10MB) routinely take >10s.
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        ok = await adapter.validate(slug, client=client)
    assert ok, f"{canonical_name!r}: {provider}={slug!r} returned False (board missing?)"
