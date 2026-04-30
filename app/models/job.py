import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str  # adzuna, greenhouse_board, jsearch, remoteok, remotive
    external_id: str
    title: str
    company_name: str
    location: str | None = None
    workplace_type: str | None = None  # remote, hybrid, onsite
    description_md: str | None = None
    description_clean: str | None = None  # markdown, populated at ingestion by html_cleaner
    salary: str | None = None
    contract_type: str | None = None
    apply_url: str
    posted_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    is_active: bool = True

    __table_args__ = (
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
