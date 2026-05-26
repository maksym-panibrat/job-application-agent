import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Protocol

FREE_COMPANY_LIMIT = 5
PAID_COMPANY_LIMIT = 100
FREE_PLAN = "free"
PAID_PLAN = "paid"
ACTIVE_STATUS = "active"
INACTIVE_STATUS = "inactive"


class SubscriptionUser(Protocol):
    subscription_plan: str
    subscription_status: str


class SearchSettings(Protocol):
    search_auto_pause_days: int


class CompanyFollowLimitError(ValueError):
    def __init__(self, limit: int) -> None:
        account_type = "Paid" if limit == PAID_COMPANY_LIMIT else "Free"
        super().__init__(f"{account_type} accounts can follow up to {limit} companies.")


def is_paid_active(user: SubscriptionUser) -> bool:
    return user.subscription_plan == PAID_PLAN and user.subscription_status == ACTIVE_STATUS


def company_follow_limit(user: SubscriptionUser) -> int:
    if is_paid_active(user):
        return PAID_COMPANY_LIMIT
    return FREE_COMPANY_LIMIT


def next_search_expiry(now: datetime, settings: SearchSettings) -> datetime:
    return now + timedelta(days=settings.search_auto_pause_days)


def dedupe_company_ids(company_ids: Iterable[uuid.UUID | str]) -> list[uuid.UUID]:
    deduped: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    for company_id in company_ids:
        normalized_id = company_id if isinstance(company_id, uuid.UUID) else uuid.UUID(company_id)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        deduped.append(normalized_id)

    return deduped


def validate_company_follow_change(
    user: SubscriptionUser,
    current_ids: Iterable[uuid.UUID | str],
    requested_ids: Iterable[uuid.UUID | str],
) -> list[uuid.UUID]:
    limit = company_follow_limit(user)
    current_deduped_ids = dedupe_company_ids(current_ids)
    requested_deduped_ids = dedupe_company_ids(requested_ids)

    if len(requested_deduped_ids) <= limit:
        return requested_deduped_ids

    current_id_set = set(current_deduped_ids)
    requested_id_set = set(requested_deduped_ids)
    if len(current_deduped_ids) > limit and requested_id_set < current_id_set:
        return requested_deduped_ids

    raise CompanyFollowLimitError(limit)
