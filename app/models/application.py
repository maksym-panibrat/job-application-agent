import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class Application(SQLModel, table=True):
    __tablename__ = "applications"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="jobs.id")
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    status: str = "pending_review"  # pending_review, approved, applied, dismissed, auto_rejected
    generation_status: str = "none"  # none, pending, generating, ready, failed
    generation_attempts: int = 0
    match_score: float | None = None
    match_rationale: str | None = None
    match_strengths: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    match_gaps: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    user_interest: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        sa.UniqueConstraint("job_id", "profile_id", name="uq_applications_job_profile"),
        sa.Index("ix_applications_dashboard", "profile_id", "status", "match_score"),
    )


class GeneratedDocument(SQLModel, table=True):
    __tablename__ = "generated_documents"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    application_id: uuid.UUID = Field(foreign_key="applications.id")
    doc_type: str  # tailored_resume, cover_letter, custom_answers
    content_md: str
    user_edited_md: str | None = None
    generation_model: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
