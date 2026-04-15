import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class Application(SQLModel, table=True):
    __tablename__ = "applications"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="jobs.id", unique=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    status: str = "pending_review"  # pending_review, approved, applied, dismissed
    generation_status: str = "pending"  # pending, generating, ready, failed
    match_score: float | None = None
    match_rationale: str | None = None
    match_strengths: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    match_gaps: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GeneratedDocument(SQLModel, table=True):
    __tablename__ = "generated_documents"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    application_id: uuid.UUID = Field(foreign_key="applications.id")
    doc_type: str  # tailored_resume, cover_letter, custom_answers
    content_md: str
    user_edited_md: str | None = None
    generation_model: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
