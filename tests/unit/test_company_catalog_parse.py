"""Tests for catalog YAML parsing + validation."""

import pytest

from app.services.company_catalog import Catalog, parse_catalog


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
    with pytest.raises(ValueError, match="invalid YAML"):
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


def test_parse_row_with_tags():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
    tags: [fintech, infra, b2b]
"""
    catalog = parse_catalog(raw)
    assert catalog.companies[0].tags == ["fintech", "infra", "b2b"]


def test_parse_row_without_tags_defaults_to_empty():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
"""
    catalog = parse_catalog(raw)
    assert catalog.companies[0].tags == []


def test_parse_row_with_empty_tags_list():
    raw = """
companies:
  - canonical_name: Stripe
    providers:
      greenhouse: stripe
    tags: []
"""
    catalog = parse_catalog(raw)
    assert catalog.companies[0].tags == []
