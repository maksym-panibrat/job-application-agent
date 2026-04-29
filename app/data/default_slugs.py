"""Hand-picked Greenhouse-active companies seeded for users with empty slug lists.

Verified live via tests/integration/test_default_slugs_live.py (nightly cron).
If a slug starts 404'ing, replace it here AND remove from any active profiles.

Selection criteria: large engineering org (>=50 open jobs typical), uses Greenhouse
public board, mix of well-known and high-quality smaller companies.
"""

DEFAULT_SLUGS: list[str] = [
    "airbnb",
    "stripe",
    "dropbox",
    "vercel",
    "instacart",
    "gusto",
    "robinhood",
    "doordashusa",
    "scaleai",
    "rampnetwork",
    "anthropic",
    "samsara",
    "datadog",
    "cloudflare",
    "asana",
]
