"""Reconcile the curated ATS catalog with public provider boards.

A missing board is expected when a company migrates ATS platforms.  This module
classifies confirmed misses separately from transient upstream failures, then
removes only confirmed-invalid provider slugs from the hand-curated YAML while
preserving its comments and formatting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.services.company_catalog import Catalog, parse_catalog
from app.sources import SOURCES
from app.sources.base import TransientFetchError

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "catalog" / "companies.yaml"
)
DEFAULT_TIMEOUT = 30.0
DEFAULT_TRANSIENT_RETRIES = 2
DEFAULT_RETRY_DELAY = 5.0
_ENTRY_START = re.compile(r"^  - canonical_name:")
_PROVIDER_LINE = re.compile(r"^      (?P<provider>greenhouse|lever|ashby):")


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcomes of one live catalog probe.

    `invalid_pairs` contains only confirmed `validate() == False` results.
    Transient errors are recorded for operators but never cause configuration
    removal, preventing a short provider outage from deleting valid entries.
    """

    invalid_pairs: list[tuple[str, str]]
    transient_errors: list[dict[str, str]]


class CatalogValidatingSource(Protocol):
    async def validate(self, slug: str, *, client: httpx.AsyncClient | None = None) -> bool: ...


async def find_invalid_provider_slugs(
    catalog: Catalog,
    *,
    sources: Mapping[str, CatalogValidatingSource],
    client: httpx.AsyncClient | None = None,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
    missing_confirmations: int = 2,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    max_concurrency: int = 10,
) -> ReconciliationResult:
    """Probe every catalog pair and return confirmed misses plus soft errors.

    A provider slug is removable only after `missing_confirmations` consecutive
    `False` responses in this run.  Any successful or transient response before
    that threshold leaves the configuration intact.  Bounded concurrency keeps
    a provider outage below the scheduled workflow timeout.
    """
    if missing_confirmations < 1:
        raise ValueError("missing_confirmations must be at least 1")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrency)

    async def probe(company: str, provider: str, slug: str) -> tuple[bool, dict[str, str] | None]:
        adapter = sources.get(provider)
        if adapter is None:
            return False, {
                "company": company,
                "provider": provider,
                "slug": slug,
                "error": "unknown provider",
            }

        last_error: TransientFetchError | None = None
        missing_count = 0
        attempts = max(transient_retries + 1, missing_confirmations)
        async with semaphore:
            for attempt in range(attempts):
                try:
                    if await adapter.validate(slug, client=client):
                        return False, None
                except TransientFetchError as exc:
                    last_error = exc
                    missing_count = 0
                else:
                    missing_count += 1
                    if missing_count >= missing_confirmations:
                        return True, None

                if attempt < attempts - 1:
                    await asyncio.sleep(retry_delay)

        return False, {
            "company": company,
            "provider": provider,
            "slug": slug,
            "error": str(last_error) if last_error is not None else "missing board not reconfirmed",
        }

    pairs = [
        (row.canonical_name, provider, slug)
        for row in catalog.companies
        for provider, slug in row.provider_slugs_dict.items()
    ]
    outcomes = await asyncio.gather(*(probe(*pair) for pair in pairs))
    invalid_pairs = [
        (provider, slug)
        for (_, provider, slug), (is_invalid, _) in zip(pairs, outcomes, strict=True)
        if is_invalid
    ]
    transient_errors = [error for _, error in outcomes if error is not None]
    return ReconciliationResult(invalid_pairs=invalid_pairs, transient_errors=transient_errors)


def prune_invalid_provider_slugs(
    raw: str, invalid_pairs: set[tuple[str, str]]
) -> tuple[str, list[str]]:
    """Remove confirmed-invalid provider slugs from catalog YAML source text.

    A company with no remaining provider is removed entirely.  The function
    deliberately edits line ranges rather than YAML round-tripping so curator
    comments and the existing hand-maintained layout remain intact.
    """
    if not invalid_pairs:
        return raw, []

    catalog = parse_catalog(raw)
    lines = raw.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if _ENTRY_START.match(line)]
    if len(starts) != len(catalog.companies):
        raise ValueError("catalog entry boundaries do not match parsed companies")

    updated_parts: list[str] = []
    removed: list[str] = []
    previous_end = 0
    for index, row in enumerate(catalog.companies):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        updated_parts.extend(lines[previous_end:start])
        previous_end = end

        targets = {
            provider: slug
            for provider, slug in row.provider_slugs_dict.items()
            if (provider, slug) in invalid_pairs
        }
        if not targets:
            updated_parts.extend(lines[start:end])
            continue

        removed.extend(
            f"{row.canonical_name}:{provider}={slug}" for provider, slug in targets.items()
        )
        if len(targets) == len(row.provider_slugs_dict):
            continue

        found: set[str] = set()
        for line in lines[start:end]:
            match = _PROVIDER_LINE.match(line)
            if match is not None and match.group("provider") in targets:
                found.add(match.group("provider"))
                continue
            updated_parts.append(line)
        if found != set(targets):
            raise ValueError(f"could not locate provider lines for {row.canonical_name!r}")

    updated_parts.extend(lines[previous_end:])
    updated = "".join(updated_parts)
    parse_catalog(updated)
    return updated, removed


async def reconcile_catalog(
    catalog_path: Path,
    *,
    write: bool,
    timeout: float = DEFAULT_TIMEOUT,
    transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> dict:
    """Probe `catalog_path`, optionally write confirmed-invalid removals, and report."""
    raw = catalog_path.read_text()
    catalog = parse_catalog(raw)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        result = await find_invalid_provider_slugs(
            catalog,
            sources=SOURCES,
            client=client,
            transient_retries=transient_retries,
            retry_delay=retry_delay,
        )
    updated, removed = prune_invalid_provider_slugs(raw, set(result.invalid_pairs))
    if write and updated != raw:
        catalog_path.write_text(updated)
    return {
        "catalog": str(catalog_path),
        "removed": removed,
        "transient_errors": result.transient_errors,
        "changed": updated != raw,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove confirmed-invalid ATS slugs from the catalog"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--write", action="store_true", help="Write removals back to the catalog")
    parser.add_argument("--report", type=Path, help="Write a JSON reconciliation report")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--transient-retries", type=int, default=DEFAULT_TRANSIENT_RETRIES)
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY)
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    report = await reconcile_catalog(
        args.catalog,
        write=args.write,
        timeout=args.timeout,
        transient_retries=args.transient_retries,
        retry_delay=args.retry_delay,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.write_text(f"{rendered}\n")
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
