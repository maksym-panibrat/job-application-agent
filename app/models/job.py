import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str  # adzuna, greenhouse, lever, ashby
    external_id: str
    ats_type: str | None = None
    title: str
    company_name: str
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_md: str | None = None
    apply_url: str
    supports_api_apply: bool = False
    posted_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    __table_args__ = (
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
