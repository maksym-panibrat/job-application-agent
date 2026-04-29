"""Integration tests for slug_fetches model + slug_registry_service."""

import pytest
from app.models.slug_fetch import SlugFetch
from sqlmodel import select


@pytest.mark.asyncio
async def test_slug_fetch_round_trip(db_session):
    row = SlugFetch(source="greenhouse_board", slug="airbnb")
    db_session.add(row)
    await db_session.commit()

    result = await db_session.execute(
        select(SlugFetch).where(
            SlugFetch.source == "greenhouse_board",
            SlugFetch.slug == "airbnb",
        )
    )
    fetched = result.scalar_one()
    assert fetched.is_invalid is False
    assert fetched.consecutive_404_count == 0
    assert fetched.last_fetched_at is None
