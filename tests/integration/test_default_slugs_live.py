"""Online validation of the default-slugs catalog.

Marked `nightly` so it runs on the cron-only CI job, not on every PR
(prevents transient Greenhouse outages from blocking unrelated work)."""

import httpx
import pytest

from app.data.default_slugs import DEFAULT_SLUGS

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards"


@pytest.mark.nightly
@pytest.mark.parametrize("slug", DEFAULT_SLUGS)
def test_default_slug_is_live(slug):
    resp = httpx.get(f"{GREENHOUSE}/{slug}", timeout=10.0)
    assert resp.status_code == 200, (
        f"Default slug `{slug}` returned {resp.status_code} — "
        f"replace it in app/data/default_slugs.py."
    )
