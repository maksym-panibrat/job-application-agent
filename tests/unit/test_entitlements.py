import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.entitlements import (
    FREE_COMPANY_LIMIT,
    PAID_COMPANY_LIMIT,
    CompanyFollowLimitError,
    company_follow_limit,
    dedupe_company_ids,
    is_paid_active,
    next_search_expiry,
    validate_company_follow_change,
)


def _user(plan: str = "free", status: str = "inactive", period_end=None) -> SimpleNamespace:
    return SimpleNamespace(
        subscription_plan=plan,
        subscription_status=status,
        subscription_current_period_end=period_end,
    )


def _ids(count: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(count)]


def test_is_paid_active_requires_paid_plan_and_active_status():
    assert is_paid_active(_user(plan="paid", status="active")) is True
    assert is_paid_active(_user(plan="paid", status="inactive")) is False
    assert is_paid_active(_user(plan="free", status="active")) is False


def test_paid_active_ignores_current_period_end_metadata():
    expired_at = datetime(2024, 1, 1, tzinfo=UTC)
    assert is_paid_active(_user(plan="paid", status="active", period_end=expired_at)) is True


def test_company_follow_limit_uses_paid_active_policy():
    assert company_follow_limit(_user(plan="free", status="inactive")) == FREE_COMPANY_LIMIT
    assert company_follow_limit(_user(plan="paid", status="inactive")) == FREE_COMPANY_LIMIT
    assert company_follow_limit(_user(plan="paid", status="active")) == PAID_COMPANY_LIMIT


def test_next_search_expiry_adds_configured_pause_days():
    now = datetime(2026, 5, 25, 12, 30, tzinfo=UTC)
    settings = SimpleNamespace(search_auto_pause_days=14)

    assert next_search_expiry(now, settings) == now + timedelta(days=14)


def test_dedupe_company_ids_preserves_order_and_normalizes_strings():
    first = uuid.uuid4()
    second = uuid.uuid4()

    assert dedupe_company_ids([str(first), second, first, str(second)]) == [first, second]


def test_free_users_can_follow_five_companies():
    requested_ids = _ids(FREE_COMPANY_LIMIT)

    assert validate_company_follow_change(_user(), [], requested_ids) == requested_ids


def test_free_users_cannot_follow_six_companies():
    with pytest.raises(
        CompanyFollowLimitError,
        match="Free accounts can follow up to 5 companies.",
    ):
        validate_company_follow_change(_user(), [], _ids(FREE_COMPANY_LIMIT + 1))


def test_paid_active_users_can_follow_one_hundred_companies():
    requested_ids = _ids(PAID_COMPANY_LIMIT)

    assert (
        validate_company_follow_change(
            _user(plan="paid", status="active"),
            [],
            requested_ids,
        )
        == requested_ids
    )


def test_downgraded_over_limit_user_can_save_removal_only_subset():
    current_ids = _ids(8)
    requested_ids = current_ids[:6]

    assert validate_company_follow_change(_user(), current_ids, requested_ids) == requested_ids


def test_downgraded_over_limit_user_cannot_swap_new_company_while_still_over_limit():
    current_ids = _ids(8)
    requested_ids = [*current_ids[:5], uuid.uuid4()]

    with pytest.raises(
        CompanyFollowLimitError,
        match="Free accounts can follow up to 5 companies.",
    ):
        validate_company_follow_change(_user(), current_ids, requested_ids)
