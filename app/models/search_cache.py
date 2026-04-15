import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class JobSearchCache(SQLModel, table=True):
    __tablename__ = "job_search_cache"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str  # adzuna, linkedin, etc.
    query_hash: str = Field(unique=True, index=True)
    query: str
    location: str | None = None
    results: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

    __table_args__ = (
        sa.Index("ix_search_cache_lookup", "source", "query_hash", "expires_at"),
    )
