"""SQLModel for work_queue rows. Schema owned by alembic; this file is
type-safe ORM access. Spec § Schema."""
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class WorkQueueStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class WorkQueue(SQLModel, table=True):
    __tablename__ = "work_queue"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    job_type: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    status: WorkQueueStatus = Field(
        default=WorkQueueStatus.PENDING,
        sa_column=Column(Text, nullable=False, server_default=text("'pending'")),
    )
    enqueued_at: datetime = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )
    claimed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    claimed_by: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    not_before: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    attempts: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    last_error: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    dedupe_key: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
