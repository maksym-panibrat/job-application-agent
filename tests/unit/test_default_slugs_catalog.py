"""Catalog of curated Greenhouse default slugs.

Online validity is exercised separately by tests/integration/test_default_slugs_live.py
(marked `nightly`). This file only checks shape/uniqueness so PRs aren't blocked
by transient Greenhouse outages."""

from app.data.default_slugs import DEFAULT_SLUGS


def test_catalog_size_in_band():
    assert 10 <= len(DEFAULT_SLUGS) <= 20


def test_all_lowercase_kebab():
    import re

    for slug in DEFAULT_SLUGS:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug), slug


def test_unique():
    assert len(DEFAULT_SLUGS) == len(set(DEFAULT_SLUGS))
