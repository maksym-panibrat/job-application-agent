from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class SlugFetch(SQLModel, table=True):
    __tablename__ = "slug_fetches"
    source: str = Field(primary_key=True)
    slug: str = Field(primary_key=True)
    last_fetched_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_attempted_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_status: str | None = None
    consecutive_404_count: int = 0
    consecutive_5xx_count: int = 0
    is_invalid: bool = False
    invalid_reason: str | None = None
    queued_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    claimed_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
