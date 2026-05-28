import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import ConfigDict
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

SUBSCRIPTION_STATUSES = ("active", "canceled", "expired", "refunded", "chargeback", "revoked")
SUBSCRIPTION_EVENT_TYPES = (
    "subscription_created",
    "subscription_renewed",
    "subscription_canceled",
    "subscription_expired",
    "subscription_refunded",
    "subscription_chargeback",
    "subscription_revoked",
    "subscription_reactivated",
    "subscription_plan_changed",
)
ENGAGEMENT_EVENT_TYPES = (
    "company_followed",
    "company_unfollowed",
    "profile_updated",
    "resume_uploaded",
    "application_dismissed",
    "application_applied",
    "chat_message_sent",
    "search_resumed",
)
ENTITLEMENT_DECISION_TYPES = (
    "follow_limit_applied",
    "follow_limit_rejected",
    "subscription_plan_rejected",
    "search_expiry_seeded",
    "search_expiry_extended",
    "search_paused",
    "paid_entitlement_activated",
    "paid_entitlement_ended",
    "over_limit_companies_preserved",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _check_in(column_name: str, values: tuple[str, ...]) -> str:
    return f"{column_name} IN ({','.join(repr(value) for value in values)})"


class SubscriptionPlan(SQLModel, table=True):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        sa.Index("ix_subscription_plans_tier", "tier"),
        sa.UniqueConstraint("tier", name="uq_subscription_plans_tier"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tier: str = Field(sa_column=Column(Text, nullable=False))
    display_name: str = Field(sa_column=Column(Text, nullable=False))
    followed_company_limit: int = Field(nullable=False)
    valid_from: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    valid_until: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class SubscriptionAccount(SQLModel, table=True):
    __tablename__ = "subscription_accounts"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider", "provider_customer_id", name="uq_subscription_accounts_provider_customer"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    provider: str = Field(sa_column=Column(Text, nullable=False))
    provider_customer_id: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class Subscription(SQLModel, table=True):
    __tablename__ = "subscriptions"
    __table_args__ = (
        sa.Index("ix_subscriptions_provider", "provider"),
        sa.Index("ix_subscriptions_status", "status"),
        sa.UniqueConstraint(
            "provider", "provider_subscription_id", name="uq_subscriptions_provider_subscription"
        ),
        CheckConstraint(
            _check_in("status", SUBSCRIPTION_STATUSES),
            name="ck_subscriptions_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    subscription_account_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("subscription_accounts.id"), index=True, nullable=False),
    )
    plan_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("subscription_plans.id"), index=True, nullable=False),
    )
    provider: str = Field(sa_column=Column(Text, nullable=False))
    provider_subscription_id: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(sa_column=Column(Text, nullable=False))
    current_period_start: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    current_period_end: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    canceled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class SubscriptionEvent(SQLModel, table=True):
    __tablename__ = "subscription_events"
    __table_args__ = (
        sa.Index("ix_subscription_events_event_type", "event_type"),
        sa.Index("ix_subscription_events_provider", "provider"),
        sa.Index("ix_subscription_events_occurred_at", "occurred_at"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_subscription_events_provider_event"
        ),
        CheckConstraint(
            _check_in("event_type", SUBSCRIPTION_EVENT_TYPES),
            name="ck_subscription_events_event_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    subscription_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("subscriptions.id"), index=True, nullable=False),
    )
    event_type: str = Field(sa_column=Column(Text, nullable=False))
    provider: str = Field(sa_column=Column(Text, nullable=False))
    provider_event_id: str = Field(sa_column=Column(Text, nullable=False))
    occurred_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )


class EngagementEvent(SQLModel, table=True):
    __tablename__ = "engagement_events"
    model_config = ConfigDict(populate_by_name=True)

    __table_args__ = (
        sa.Index("ix_engagement_events_event_type", "event_type"),
        sa.Index("ix_engagement_events_subject_id", "subject_id"),
        sa.Index("ix_engagement_events_source", "source"),
        sa.Index("ix_engagement_events_occurred_at", "occurred_at"),
        sa.Index(
            "ix_engagement_events_profile_occurred_at",
            "profile_id",
            "occurred_at",
        ),
        CheckConstraint(
            _check_in("event_type", ENGAGEMENT_EVENT_TYPES),
            name="ck_engagement_events_event_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    profile_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("user_profiles.id"), index=True, nullable=False),
    )
    event_type: str = Field(sa_column=Column(Text, nullable=False))
    subject_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    subject_id: uuid.UUID | None = Field(default=None)
    source: str = Field(
        default="api",
        sa_column=Column(Text, nullable=False, server_default=text("'api'")),
    )
    occurred_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    event_metadata: dict = Field(
        default_factory=dict,
        alias="metadata",
        sa_column=Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )


class EntitlementDecision(SQLModel, table=True):
    __tablename__ = "entitlement_decisions"
    __table_args__ = (
        sa.Index("ix_entitlement_decisions_decision_type", "decision_type"),
        sa.Index("ix_entitlement_decisions_source_event_id", "source_event_id"),
        sa.Index("ix_entitlement_decisions_decided_at", "decided_at"),
        CheckConstraint(
            _check_in("decision_type", ENTITLEMENT_DECISION_TYPES),
            name="ck_entitlement_decisions_decision_type",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    profile_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("user_profiles.id"), index=True, nullable=False),
    )
    decision_type: str = Field(sa_column=Column(Text, nullable=False))
    previous_value: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    next_value: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    reason: str = Field(sa_column=Column(Text, nullable=False))
    source_event_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_event_id: uuid.UUID | None = Field(default=None)
    decided_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
