"""Integration tests for slug_fetches model + slug_registry_service."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlmodel import select

from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user_profile import UserProfile
from app.services import slug_registry_service
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE


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
async def test_validate_slug_writes_row_on_success(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/airbnb").mock(
            return_value=httpx.Response(200, json={"name": "Airbnb", "content": "<p/>"})
        )
        ok = await slug_registry_service.validate_slug("greenhouse", "airbnb", db_session)
    assert ok is True
    row = await slug_registry_service.get("greenhouse", "airbnb", db_session)
    assert row is not None
    assert row.last_status == "ok"
    assert row.last_fetched_at is None  # validate is existence-only


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404_and_writes_no_row(db_session):
    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/openai").mock(return_value=httpx.Response(404))
        ok = await slug_registry_service.validate_slug("greenhouse", "openai", db_session)
    assert ok is False
    row = await slug_registry_service.get("greenhouse", "openai", db_session)
    assert row is None


@pytest.mark.asyncio
async def test_mark_fetched_ok_resets_counters(db_session):
    await slug_registry_service.mark_fetched("greenhouse", "stripe", "ok", db_session)
    row = await slug_registry_service.get("greenhouse", "stripe", db_session)
    assert row.last_status == "ok"
    assert row.consecutive_404_count == 0
    assert row.consecutive_5xx_count == 0
    assert row.last_fetched_at is not None
    assert row.queued_at is None
    assert row.claimed_at is None


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
async def test_enqueue_stale_inserts_for_unknown_slugs(db_session):
    profile = await _profile_with_slugs(db_session, "airbnb", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert sorted(queued) == ["airbnb", "stripe"]
    for slug in ["airbnb", "stripe"]:
        row = await slug_registry_service.get("greenhouse", slug, db_session)
        assert row.queued_at is not None


@pytest.mark.asyncio
async def test_enqueue_stale_skips_fresh_slugs(db_session):
    await slug_registry_service.mark_fetched("greenhouse", "airbnb", "ok", db_session)
    profile = await _profile_with_slugs(db_session, "airbnb", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert queued == ["stripe"]


@pytest.mark.asyncio
async def test_enqueue_stale_skips_invalid_slugs(db_session):
    # Two strikes → invalid
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    await slug_registry_service.mark_fetched("greenhouse", "openai", "invalid", db_session)
    profile = await _profile_with_slugs(db_session, "openai", "stripe")
    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert queued == ["stripe"]


@pytest.mark.asyncio
async def test_next_pending_claims_and_orders_by_queued_at(db_session):
    profile = await _profile_with_slugs(db_session, "airbnb", "stripe", "notion")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    rows = await slug_registry_service.next_pending(db_session, limit=2)
    assert len(rows) == 2
    assert all(r.claimed_at is not None for r in rows)


@pytest.mark.asyncio
async def test_next_pending_skips_claimed_within_lease(db_session):
    profile = await _profile_with_slugs(db_session, "airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    first = await slug_registry_service.next_pending(db_session, limit=10)
    assert len(first) == 1
    second = await slug_registry_service.next_pending(db_session, limit=10)
    assert second == []  # locked by lease


@pytest.mark.asyncio
async def test_next_pending_reclaims_after_lease_expires(db_session):
    profile = await _profile_with_slugs(db_session, "airbnb")
    await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    rows = await slug_registry_service.next_pending(db_session, limit=10)
    # Force-expire the lease
    rows[0].claimed_at = datetime.now(UTC) - timedelta(seconds=600)
    db_session.add(rows[0])
    await db_session.commit()

    again = await slug_registry_service.next_pending(db_session, limit=10, lease_seconds=300)
    assert len(again) == 1


@pytest.mark.asyncio
async def test_enqueue_stale_walks_all_provider_slugs(db_session, seeded_user):
    """A Company with two provider_slugs entries queues two SlugFetch rows."""
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

    queued = await slug_registry_service.enqueue_stale(profile, db_session, ttl_hours=6)
    assert sorted(queued) == ["linear", "linear"]

    rows = (
        (await db_session.execute(select(SlugFetch).where(SlugFetch.slug == "linear")))
        .scalars()
        .all()
    )
    sources = sorted(r.source for r in rows)
    assert sources == ["ashby", "greenhouse"]
    assert all(r.queued_at is not None for r in rows)


@pytest.mark.asyncio
async def test_enqueue_stale_skips_unfollowable_companies(db_session, seeded_user):
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

    queued = await slug_registry_service.enqueue_stale(profile, db_session)
    assert queued == []
