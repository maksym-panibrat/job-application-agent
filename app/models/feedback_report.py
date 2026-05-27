from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class FeedbackReport(SQLModel, table=True):
    __tablename__ = "feedback_reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    user_email: str = Field(index=True, max_length=320)
    category: str = Field(index=True, max_length=32)
    message: str = Field(sa_column=Column(Text, nullable=False))
    diagnostics: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    notification_status: str = Field(index=True, max_length=32)
    notification_error: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            index=True,
            server_default=text("now()"),
        ),
    )
