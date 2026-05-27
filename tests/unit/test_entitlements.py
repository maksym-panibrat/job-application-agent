import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.entitlements import (
    FREE_COMPANY_LIMIT,
    FREE_TIER,
    PAID_COMPANY_LIMIT,
    CompanyFollowLimitError,
    EffectiveEntitlements,
    SubscriptionSnapshot,
    company_follow_limit,
    dedupe_company_ids,
    effective_entitlements,
    next_search_expiry,
    validate_company_follow_change,
)

NOW = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)


def _subscription(
    *,
    tier: str = "paid",
    status: str = "active",
    current_period_end: datetime | None = None,
    followed_company_limit: int = PAID_COMPANY_LIMIT,
) -> SubscriptionSnapshot:
    return SubscriptionSnapshot(
        tier=tier,
        status=status,
        current_period_end=current_period_end or NOW + timedelta(days=7),
        followed_company_limit=followed_company_limit,
    )


def _entitlements(limit: int = FREE_COMPANY_LIMIT) -> EffectiveEntitlements:
    return EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status=None,
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=limit,
    )


def _ids(count: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(count)]


def test_active_before_period_end_grants_paid_access_and_limit():
    entitlements = effective_entitlements(_subscription(status="active"), now=NOW)

    assert entitlements == EffectiveEntitlements(
        tier="paid",
        subscription_status="active",
        paid_access=True,
        search_auto_pause=False,
        followed_company_limit=PAID_COMPANY_LIMIT,
    )
    assert company_follow_limit(entitlements) == PAID_COMPANY_LIMIT


def test_canceled_before_period_end_grants_paid_access_and_limit():
    entitlements = effective_entitlements(_subscription(status="canceled"), now=NOW)

    assert entitlements == EffectiveEntitlements(
        tier="paid",
        subscription_status="canceled",
        paid_access=True,
        search_auto_pause=False,
        followed_company_limit=PAID_COMPANY_LIMIT,
    )
    assert company_follow_limit(entitlements) == PAID_COMPANY_LIMIT


@pytest.mark.parametrize("status", ["expired", "refunded", "chargeback", "revoked"])
def test_terminal_statuses_grant_free_access_and_limit(status: str):
    entitlements = effective_entitlements(_subscription(status=status), now=NOW)

    assert entitlements == EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status=status,
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=FREE_COMPANY_LIMIT,
    )


def test_active_with_past_period_end_grants_free_access_and_limit():
    entitlements = effective_entitlements(
        _subscription(status="active", current_period_end=NOW - timedelta(seconds=1)),
        now=NOW,
    )

    assert entitlements == EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status="active",
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=FREE_COMPANY_LIMIT,
    )


def test_no_subscription_grants_free_access_and_no_subscription_status():
    entitlements = effective_entitlements(None, now=NOW)

    assert entitlements == EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status=None,
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=FREE_COMPANY_LIMIT,
    )


def test_active_subscription_trusts_snapshot_limit_even_when_plan_validity_expired():
    entitlements = effective_entitlements(
        _subscription(
            tier="expired-paid-plan",
            status="active",
            current_period_end=NOW + timedelta(days=1),
            followed_company_limit=42,
        ),
        now=NOW,
    )

    assert entitlements == EffectiveEntitlements(
        tier="expired-paid-plan",
        subscription_status="active",
        paid_access=True,
        search_auto_pause=False,
        followed_company_limit=42,
    )
    assert company_follow_limit(entitlements) == 42


def test_next_search_expiry_adds_configured_pause_days():
    settings = SimpleNamespace(search_auto_pause_days=14)

    assert next_search_expiry(NOW, settings) == NOW + timedelta(days=14)


def test_dedupe_company_ids_preserves_order_and_normalizes_strings():
    first = uuid.uuid4()
    second = uuid.uuid4()

    assert dedupe_company_ids([str(first), second, first, str(second)]) == [first, second]


def test_free_users_can_follow_five_companies():
    requested_ids = _ids(FREE_COMPANY_LIMIT)

    assert validate_company_follow_change(_entitlements(), [], requested_ids) == requested_ids


def test_free_users_cannot_follow_six_companies():
    with pytest.raises(
        CompanyFollowLimitError,
        match="Free accounts can follow up to 5 companies.",
    ):
        validate_company_follow_change(_entitlements(), [], _ids(FREE_COMPANY_LIMIT + 1))


def test_paid_users_can_follow_one_hundred_companies():
    entitlements = effective_entitlements(_subscription(), now=NOW)
    requested_ids = _ids(PAID_COMPANY_LIMIT)

    assert validate_company_follow_change(entitlements, [], requested_ids) == requested_ids


def test_downgraded_over_limit_user_can_save_removal_only_subset():
    current_ids = _ids(8)
    requested_ids = current_ids[:6]

    assert (
        validate_company_follow_change(_entitlements(), current_ids, requested_ids)
        == requested_ids
    )


def test_downgraded_over_limit_user_cannot_add_company_while_still_over_limit():
    current_ids = _ids(8)
    requested_ids = [*current_ids, uuid.uuid4()]

    with pytest.raises(
        CompanyFollowLimitError,
        match="Free accounts can follow up to 5 companies.",
    ):
        validate_company_follow_change(_entitlements(), current_ids, requested_ids)


def test_downgraded_over_limit_user_cannot_swap_company_while_still_over_limit():
    current_ids = _ids(8)
    requested_ids = [*current_ids[:5], uuid.uuid4()]

    with pytest.raises(
        CompanyFollowLimitError,
        match="Free accounts can follow up to 5 companies.",
    ):
        validate_company_follow_change(_entitlements(), current_ids, requested_ids)
