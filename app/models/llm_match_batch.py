import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


BATCH_STATUS_BUILDING = "building"
BATCH_STATUS_SUBMITTED = "submitted"
BATCH_STATUS_IMPORTING = "importing"
BATCH_STATUS_DONE = "done"
BATCH_STATUS_FAILED = "failed"
ACTIVE_BATCH_STATUSES = (
    BATCH_STATUS_BUILDING,
    BATCH_STATUS_SUBMITTED,
    BATCH_STATUS_IMPORTING,
)

ITEM_STATUS_SUBMITTED = "submitted"
ITEM_STATUS_RETRYABLE_FAILED = "retryable_failed"
ITEM_STATUS_TERMINAL_FAILED = "terminal_failed"
ITEM_STATUS_IMPORTED = "imported"
ACTIVE_ITEM_STATUSES = (ITEM_STATUS_SUBMITTED,)


class LLMMatchBatch(SQLModel, table=True):
    __tablename__ = "llm_match_batches"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    provider: str = Field(sa_column=Column(sa.Text, nullable=False))
    provider_batch_id: str | None = Field(default=None, sa_column=Column(sa.Text))
    model: str = Field(sa_column=Column(sa.Text, nullable=False))
    prompt_version: str = Field(sa_column=Column(sa.Text, nullable=False))
    status: str = Field(
        default=BATCH_STATUS_BUILDING,
        sa_column=Column(sa.Text, nullable=False),
    )
    submitted_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    next_poll_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    last_polled_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    last_error: str | None = Field(default=None, sa_column=Column(sa.Text))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Keep partial-index predicates aligned with status constants and migration literals.
    __table_args__ = (
        sa.Index(
            "uq_llm_match_batches_one_active_per_profile",
            "profile_id",
            unique=True,
            postgresql_where=sa.text("status IN ('building', 'submitted', 'importing')"),
        ),
        sa.Index("ix_llm_match_batches_next_poll_at", "next_poll_at"),
    )


class LLMMatchBatchItem(SQLModel, table=True):
    __tablename__ = "llm_match_batch_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_id: uuid.UUID = Field(foreign_key="llm_match_batches.id")
    application_id: uuid.UUID = Field(foreign_key="applications.id")
    provider_request_key: str = Field(sa_column=Column(sa.Text, nullable=False))
    request_hash: str = Field(sa_column=Column(sa.Text, nullable=False))
    status: str = Field(
        default=ITEM_STATUS_SUBMITTED,
        sa_column=Column(sa.Text, nullable=False),
    )
    score: float | None = Field(
        default=None,
        sa_column=Column(sa.Float, nullable=True),
    )
    summary: str | None = Field(default=None, sa_column=Column(sa.Text))
    rationale: str | None = Field(default=None, sa_column=Column(sa.Text))
    strengths: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    gaps: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    error: str | None = Field(default=None, sa_column=Column(sa.Text))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Keep partial-index predicates aligned with status constants and migration literals.
    __table_args__ = (
        sa.Index(
            "uq_llm_match_batch_items_active_attempt",
            "application_id",
            "request_hash",
            unique=True,
            postgresql_where=sa.text("status = 'submitted'"),
        ),
        sa.Index("ix_llm_match_batch_items_batch_status", "batch_id", "status"),
    )
