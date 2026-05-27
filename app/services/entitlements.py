import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.subscription import Subscription, SubscriptionPlan

FREE_TIER = "free"
FREE_COMPANY_LIMIT = 5
PAID_COMPANY_LIMIT = 100
PAID_ENTITLEMENT_STATUSES = {"active", "canceled"}


@dataclass(frozen=True)
class SubscriptionSnapshot:
    tier: str
    status: str
    current_period_end: datetime
    followed_company_limit: int


@dataclass(frozen=True)
class EffectiveEntitlements:
    tier: str
    subscription_status: str | None
    paid_access: bool
    search_auto_pause: bool
    followed_company_limit: int


class SearchSettings(Protocol):
    search_auto_pause_days: int


class CompanyFollowLimitError(ValueError):
    def __init__(self, limit: int) -> None:
        account_type = "Paid" if limit > FREE_COMPANY_LIMIT else "Free"
        super().__init__(f"{account_type} accounts can follow up to {limit} companies.")


def effective_entitlements(
    subscription: SubscriptionSnapshot | None,
    now: datetime | None = None,
) -> EffectiveEntitlements:
    if subscription is None:
        return _free_entitlements(subscription_status=None)

    effective_now = now or datetime.now(UTC)
    paid_access = (
        subscription.status in PAID_ENTITLEMENT_STATUSES
        and subscription.current_period_end > effective_now
    )
    if paid_access:
        return EffectiveEntitlements(
            tier=subscription.tier,
            subscription_status=subscription.status,
            paid_access=True,
            search_auto_pause=False,
            followed_company_limit=subscription.followed_company_limit,
        )

    return _free_entitlements(subscription_status=subscription.status)


def _free_entitlements(subscription_status: str | None) -> EffectiveEntitlements:
    return EffectiveEntitlements(
        tier=FREE_TIER,
        subscription_status=subscription_status,
        paid_access=False,
        search_auto_pause=True,
        followed_company_limit=FREE_COMPANY_LIMIT,
    )


def company_follow_limit(entitlements: EffectiveEntitlements) -> int:
    return entitlements.followed_company_limit


async def get_subscription_snapshot(
    user_id: uuid.UUID,
    session: AsyncSession,
) -> SubscriptionSnapshot | None:
    result = await session.execute(
        select(Subscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == Subscription.plan_id)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None

    subscription, plan = row
    return SubscriptionSnapshot(
        tier=plan.tier,
        status=subscription.status,
        current_period_end=subscription.current_period_end,
        followed_company_limit=plan.followed_company_limit,
    )


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
    entitlements: EffectiveEntitlements,
    current_ids: Iterable[uuid.UUID | str],
    requested_ids: Iterable[uuid.UUID | str],
) -> list[uuid.UUID]:
    limit = company_follow_limit(entitlements)
    current_deduped_ids = dedupe_company_ids(current_ids)
    requested_deduped_ids = dedupe_company_ids(requested_ids)

    if len(requested_deduped_ids) <= limit:
        return requested_deduped_ids

    current_id_set = set(current_deduped_ids)
    requested_id_set = set(requested_deduped_ids)
    if len(current_deduped_ids) > limit and requested_id_set < current_id_set:
        return requested_deduped_ids

    raise CompanyFollowLimitError(limit)
