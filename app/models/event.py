"""Event log for in-app analytics — see spec section 7.

Authenticated events tie to profile_id; anonymous events tie to a
client-generated session_id (random UUID stored in sessionStorage).
Retention is 90 days, enforced by run_daily_maintenance."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    profile_id: UUID | None = Field(
        default=None,
        sa_column=Column(ForeignKey("user_profiles.id"), index=True, nullable=True),
    )
    session_id: str = Field(index=True, max_length=64)
    name: str = Field(index=True, max_length=64)
    properties: dict | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )
    user_agent: str | None = Field(default=None, max_length=512)
    path: str | None = Field(default=None, max_length=256)
