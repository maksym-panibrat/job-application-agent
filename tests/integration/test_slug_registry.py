"""Integration tests for slug_fetches model + slug_registry_service."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.services import slug_registry_service


async def _profile_with_slugs(db_session, *slugs: str) -> UserProfile:
    """Build a UserProfile whose target_company_ids point at one Company row
    per slug, all under the greenhouse provider."""
    company_ids: list[uuid.UUID] = []
    for slug in slugs:
        company = Company(
            canonical_name=slug.title(),
            normalized_key=f"{slug}-{uuid.uuid4()}",
            provider_slugs={"greenhouse": slug},
            resolved_at=datetime.now(UTC),
        )
        db_session.add(company)
        await db_session.commit()
        await db_session.refresh(company)
        company_ids.append(company.id)
    return UserProfile(
        user_id=uuid.uuid4(),
        target_company_ids=company_ids,
    )


@pytest.mark.asyncio
async def test_slug_fetch_round_trip(db_session):
    row = SlugFetch(source="greenhouse", slug="airbnb")
    db_session.add(row)
    await db_session.commit()

    result = await db_session.execute(
        select(SlugFetch).where(
            SlugFetch.source == "greenhouse",
            SlugFetch.slug == "airbnb",
        )
    )
    fetched = result.scalar_one()
    assert fetched.is_invalid is False
    assert fetched.consecutive_404_count == 0
    assert fetched.last_fetched_at is None


@pytest.mark.asyncio
async def test_mark_fetched_ok_resets_counters(db_session):
    await slug_registry_service.mark_fetched("greenhouse", "stripe", "ok", db_session)
    row = await slug_registry_service.get("greenhouse", "stripe", db_session)
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 0
    assert row.last_fetched_at is not None


@pytest.mark.asyncio
async def test_mark_fetched_invalid_increments_404_and_flips_at_2(db_session):
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    row = await slug_registry_service.get("greenhouse", "openai", db_session)
    assert row.consecutive_404_count == 1
    assert row.is_invalid is False  # one strike

    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    row = await slug_registry_service.get("greenhouse", "openai", db_session)
    assert row.consecutive_404_count == 2
    assert row.is_invalid is True  # two strikes — pruned
    assert row.invalid_reason is not None


@pytest.mark.asyncio
async def test_mark_fetched_transient_does_not_count_toward_invalid(db_session):
    for _ in range(5):
        await slug_registry_service.mark_fetched(
            "greenhouse", "flaky", "transient_error", db_session
        )
    row = await slug_registry_service.get("greenhouse", "flaky", db_session)
    assert row.is_invalid is False
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 5


@pytest.mark.asyncio
async def test_list_stale_for_profile_includes_unknown_slugs(db_session):
    profile = await _profile_with_slugs(db_session, "airbnb", "stripe")
    stale = await slug_registry_service.list_stale_for_profile(profile, db_session, ttl_hours=6)
    assert sorted(stale) == [("greenhouse", "airbnb"), ("greenhouse", "stripe")]


@pytest.mark.asyncio
async def test_list_stale_for_profile_skips_fresh_slugs(db_session):
    await slug_registry_service.mark_fetched("greenhouse", "airbnb", "ok", db_session)
    profile = await _profile_with_slugs(db_session, "airbnb", "stripe")
    stale = await slug_registry_service.list_stale_for_profile(profile, db_session, ttl_hours=6)
    assert stale == [("greenhouse", "stripe")]


@pytest.mark.asyncio
async def test_list_stale_for_profile_skips_invalid_slugs(db_session):
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    profile = await _profile_with_slugs(db_session, "openai", "stripe")
    stale = await slug_registry_service.list_stale_for_profile(profile, db_session, ttl_hours=6)
    assert stale == [("greenhouse", "stripe")]


@pytest.mark.asyncio
async def test_list_stale_for_profile_walks_all_provider_slugs(db_session, seeded_user):
    """A Company with two provider_slugs entries returns two stale pairs."""
    company = Company(
        canonical_name="Linear",
        normalized_key="linear",
        provider_slugs={"ashby": "linear", "greenhouse": "linear"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    # seeded_user has a UserProfile already; load it.
    profile = (
        await db_session.execute(
            select(UserProfile).where(UserProfile.user_id == seeded_user[0].id)
        )
    ).scalar_one()
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()

    stale = await slug_registry_service.list_stale_for_profile(profile, db_session, ttl_hours=6)
    assert sorted(stale) == [("ashby", "linear"), ("greenhouse", "linear")]


@pytest.mark.asyncio
async def test_list_stale_for_profile_skips_unfollowable_companies(db_session, seeded_user):
    company = Company(
        canonical_name="DefunctCo",
        normalized_key="defunctco",
        provider_slugs={},
        unfollowable=True,
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = (
        await db_session.execute(
            select(UserProfile).where(UserProfile.user_id == seeded_user[0].id)
        )
    ).scalar_one()
    profile.target_company_ids = [company.id]
    db_session.add(profile)
    await db_session.commit()

    stale = await slug_registry_service.list_stale_for_profile(profile, db_session)
    assert stale == []
