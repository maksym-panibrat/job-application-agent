import uuid
from datetime import datetime

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class JobSearchCache(SQLModel, table=True):
    __tablename__ = "job_search_cache"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    query_hash: str = Field(unique=True, index=True)
    query_what: str
    query_where: str | None = None
    page: int = 1
    results: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
