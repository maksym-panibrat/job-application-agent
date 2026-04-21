import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel


class Skill(SQLModel, table=True):
    __tablename__ = "skills"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    name: str
    category: str | None = None  # language, framework, cloud, domain
    proficiency: str | None = None  # expert, proficient, familiar
    years: float | None = None


class WorkExperience(SQLModel, table=True):
    __tablename__ = "work_experiences"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    company: str
    title: str
    start_date: datetime = Field(sa_column=Column(sa.DateTime(timezone=True), nullable=False))
    end_date: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    description_md: str | None = None
    technologies: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )


class UserProfile(SQLModel, table=True):
    __tablename__ = "user_profiles"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", unique=True)
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    base_resume_md: str | None = None
    base_resume_raw: bytes | None = None
    target_roles: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    target_locations: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    remote_ok: bool = True
    seniority: str | None = None
    search_keywords: list[str] = Field(
        default_factory=list, sa_column=Column(ARRAY(sa.String))
    )
    source_cursors: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    target_company_slugs: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    first_name: str | None = None
    last_name: str | None = None
    work_authorization: str | None = None
    requires_sponsorship: bool | None = None
    salary_expectation_usd: int | None = None
    available_from: str | None = None
    standard_answers: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    search_active: bool = True
    search_expires_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
