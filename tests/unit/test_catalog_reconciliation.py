import pytest

from app.services.catalog_reconciliation import (
    find_invalid_provider_slugs,
    prune_invalid_provider_slugs,
)
from app.services.company_catalog import parse_catalog
from app.sources.base import TransientFetchError


class FakeSource:
    def __init__(self, outcomes_by_slug):
        self.outcomes_by_slug = {
            slug: iter(outcomes) for slug, outcomes in outcomes_by_slug.items()
        }

    async def validate(self, slug, *, client=None):
        outcome = next(self.outcomes_by_slug[slug])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_reconciliation_prunes_only_confirmed_missing_boards():
    catalog = parse_catalog(
        """companies:
  - canonical_name: Missing
    providers:
      greenhouse: missing
  - canonical_name: Flaky
    providers:
      greenhouse: flaky
"""
    )

    result = await find_invalid_provider_slugs(
        catalog,
        sources={
            "greenhouse": FakeSource(
                {
                    "missing": [False, False],
                    "flaky": [
                        TransientFetchError("flaky", "upstream 503"),
                        TransientFetchError("flaky"),
                    ],
                }
            )
        },
        transient_retries=1,
        retry_delay=0,
    )

    assert result.invalid_pairs == [("greenhouse", "missing")]
    assert result.transient_errors == [
        {
            "company": "Flaky",
            "provider": "greenhouse",
            "slug": "flaky",
            "error": "flaky",
        }
    ]


@pytest.mark.asyncio
async def test_reconciliation_requires_consecutive_missing_responses():
    catalog = parse_catalog(
        """companies:
  - canonical_name: Intermittent
    providers:
      greenhouse: intermittent
"""
    )

    result = await find_invalid_provider_slugs(
        catalog,
        sources={
            "greenhouse": FakeSource(
                {
                    "intermittent": [
                        False,
                        TransientFetchError("intermittent", "upstream timeout"),
                        False,
                    ]
                }
            )
        },
        transient_retries=2,
        retry_delay=0,
    )

    assert result.invalid_pairs == []
    assert result.transient_errors[0]["error"] == "upstream timeout"


@pytest.mark.asyncio
async def test_reconciliation_retains_board_when_a_404_is_not_reconfirmed():
    catalog = parse_catalog(
        """companies:
  - canonical_name: Recovered
    providers:
      greenhouse: recovered
"""
    )

    result = await find_invalid_provider_slugs(
        catalog,
        sources={"greenhouse": FakeSource({"recovered": [False, True]})},
        transient_retries=1,
        retry_delay=0,
    )

    assert result.invalid_pairs == []
    assert result.transient_errors == []


def test_prune_invalid_provider_slugs_removes_only_the_dead_provider():
    raw = """# Curated choices
companies:
  - canonical_name: Alpha
    providers:
      greenhouse: alpha-old
    tags: [ai]
  - canonical_name: Beta
    providers:
      ashby: beta
      greenhouse: beta-old
    tags: [dev-tools]
"""

    updated, removed = prune_invalid_provider_slugs(
        raw,
        {("greenhouse", "alpha-old"), ("greenhouse", "beta-old")},
    )

    assert "canonical_name: Alpha" not in updated
    assert "greenhouse: alpha-old" not in updated
    assert "canonical_name: Beta" in updated
    assert "ashby: beta" in updated
    assert "greenhouse: beta-old" not in updated
    assert "# Curated choices" in updated
    assert removed == ["Alpha:greenhouse=alpha-old", "Beta:greenhouse=beta-old"]


def test_prune_invalid_provider_slugs_leaves_valid_entries_byte_for_byte():
    raw = """companies:
  - canonical_name: Alpha
    providers:
      greenhouse: alpha
    tags: [ai]
"""

    updated, removed = prune_invalid_provider_slugs(raw, {("greenhouse", "missing")})

    assert updated == raw
    assert removed == []
