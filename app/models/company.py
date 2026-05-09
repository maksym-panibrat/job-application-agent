import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    canonical_name: str = Field(sa_column=Column(sa.Text, nullable=False))
    normalized_key: str = Field(sa_column=Column(sa.Text, nullable=False, unique=True, index=True))
    provider_slugs: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    unfollowable: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    is_curated: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean, nullable=False, index=True, server_default=sa.text("false")),
    )
    resolved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
