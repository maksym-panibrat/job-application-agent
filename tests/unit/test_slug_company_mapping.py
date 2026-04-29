"""slug ↔ company_name round-trip + edge cases.

This mapping is the brittle bridge between the global Job table (keyed by
company_name) and the per-profile slug list. Locking it down with tests so
a refactor to a Company table later is safe."""

import pytest

from app.data.slug_company import company_name_to_slug, slug_to_company_name


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("airbnb", "Airbnb"),
        ("stripe", "Stripe"),
        ("dropbox-engineering", "Dropbox Engineering"),
        ("a-b-c", "A B C"),
    ],
)
def test_slug_to_company_name(slug, expected):
    assert slug_to_company_name(slug) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Airbnb", "airbnb"),
        ("Dropbox Engineering", "dropbox-engineering"),
        ("Notion Labs", "notion-labs"),
    ],
)
def test_company_name_to_slug(name, expected):
    assert company_name_to_slug(name) == expected


@pytest.mark.parametrize("slug", ["airbnb", "stripe", "dropbox-engineering"])
def test_round_trip(slug):
    """company_name_to_slug(slug_to_company_name(slug)) must equal slug."""
    assert company_name_to_slug(slug_to_company_name(slug)) == slug
