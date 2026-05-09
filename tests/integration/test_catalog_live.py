"""Live validation of the curated catalog against the real public ATS boards.

Marked `catalog_live`; only runs with --catalog-live. The nightly
validate-catalog GitHub Actions workflow invokes it; PR CI skips it.

A single test parametrized over every (provider, slug) pair in
companies.yaml. Each parametrized case fails independently so a single
broken entry doesn't mask the others.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.services.company_catalog import parse_catalog
from app.sources import SOURCES
from app.sources.base import TransientFetchError

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "app" / "data" / "catalog" / "companies.yaml"
)

# 30s — Ashby's public posting-api downloads the full board JSON on GET;
# large boards (OpenAI ~10MB) routinely take >10s on a typical home connection.
HTTP_TIMEOUT = 30.0
# Two retries on transient failures (5xx, network blip, slow read) before
# we conclude a board is broken. Without this the nightly cron opens
# tracking issues on every upstream hiccup.
TRANSIENT_RETRIES = 2
RETRY_DELAY = 5.0


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
    public board. True = exists; False = confirmed 404; TransientFetchError
    = retry; persistent transient = surface as failure (board likely down)."""
    adapter = SOURCES[provider]
    last_exc: TransientFetchError | None = None
    for attempt in range(TRANSIENT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT)) as client:
                ok = await adapter.validate(slug, client=client)
            assert ok, f"{canonical_name!r}: {provider}={slug!r} returned False (board missing?)"
            return
        except TransientFetchError as exc:
            last_exc = exc
            if attempt < TRANSIENT_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
    raise AssertionError(
        f"{canonical_name!r}: {provider}={slug!r} transient after "
        f"{TRANSIENT_RETRIES + 1} attempts: {last_exc}"
    )
