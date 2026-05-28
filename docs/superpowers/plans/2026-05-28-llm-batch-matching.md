# LLM Batch Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `fetch-slug -> batch-match` the primary matching workflow, using provider batch requests that score up to 10 same-profile applications per request.

**Architecture:** Add durable `llm_match_batches` and `llm_match_batch_items` tables, a `batch-match` worker handler, and a service layer that owns profile-level selection, deterministic rejection, token-aware packing, provider submission, polling, and import. Keep direct `match` available as a fallback path while ordinary sync enqueues profile-level `batch-match` work.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async, Alembic, PostgreSQL, pytest, provider abstraction with a fake provider first and Gemini adapter behind a feature flag.

---

## File Structure

- Create `app/models/llm_match_batch.py`: SQLModel tables and status constants for local batch ownership.
- Modify `app/models/__init__.py`: register new models for SQLModel metadata and tests.
- Create `alembic/versions/b7c8d9e0f1a2_add_llm_match_batches.py`: production schema migration.
- Create `tests/integration/test_llm_match_batch_schema.py`: schema and constraint coverage.
- Modify `app/config.py`: add feature flags and batch matching limits.
- Create `app/services/batch_match_provider.py`: provider protocol, fake provider, result types.
- Create `app/services/batch_match_packing.py`: prompt context rendering, request hash, token/byte estimate, and max-10 packing.
- Create `tests/unit/test_batch_match_packing.py`: packing and hash coverage.
- Create `app/services/batch_match_service.py`: selection, deterministic rejects, local batch creation, provider submission state transitions, polling, and import.
- Create `tests/integration/test_batch_match_service.py`: service-level database behavior with fake provider.
- Create `app/worker/handlers/batch_match.py`: `batch-match` queue handler and registration.
- Modify `app/worker/payloads.py`: add `BatchMatchPayload`.
- Modify `app/worker/main.py`: import the new handler module.
- Modify `app/worker/config.py`: rename the default fast lane setting and include `batch-match` in slow lane defaults.
- Modify `app/worker/queue_service.py`: prioritize `batch-match` after `maintenance`.
- Create `tests/integration/test_handler_batch_match.py`: worker-handler flow tests.
- Modify `app/scheduler/tasks.py`: enqueue `batch-match` per affected profile when enabled.
- Create or modify `tests/integration/test_fetch_slug_batch_match.py`: fetch-to-batch contract.
- Modify `app/api/jobs.py`: count active batch matching work as `"matching"`.
- Modify `tests/integration/test_sync_status_endpoint.py`: sync status compatibility coverage.

---

### Task 1: Add Durable Batch Schema

**Files:**
- Create: `app/models/llm_match_batch.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/b7c8d9e0f1a2_add_llm_match_batches.py`
- Create: `tests/integration/test_llm_match_batch_schema.py`

- [ ] **Step 1: Write the failing schema tests**

Create `tests/integration/test_llm_match_batch_schema.py`:

```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_llm_match_batches_table_exists(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'llm_match_batches'
                ORDER BY ordinal_position
                """
            )
        )
    ).all()
    cols = {row[0]: (row[1], row[2]) for row in rows}
    expected = {
        "id": ("uuid", "NO"),
        "profile_id": ("uuid", "NO"),
        "provider": ("text", "NO"),
        "provider_batch_id": ("text", "YES"),
        "model": ("text", "NO"),
        "prompt_version": ("text", "NO"),
        "status": ("text", "NO"),
        "submitted_at": ("timestamp with time zone", "YES"),
        "completed_at": ("timestamp with time zone", "YES"),
        "next_poll_at": ("timestamp with time zone", "YES"),
        "last_polled_at": ("timestamp with time zone", "YES"),
        "last_error": ("text", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    }
    for column, expected_shape in expected.items():
        assert cols[column] == expected_shape


@pytest.mark.asyncio
async def test_llm_match_batch_items_table_exists(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'llm_match_batch_items'
                ORDER BY ordinal_position
                """
            )
        )
    ).all()
    cols = {row[0]: (row[1], row[2]) for row in rows}
    expected = {
        "id": ("uuid", "NO"),
        "batch_id": ("uuid", "NO"),
        "application_id": ("uuid", "NO"),
        "provider_request_key": ("text", "NO"),
        "request_hash": ("text", "NO"),
        "status": ("text", "NO"),
        "score": ("double precision", "YES"),
        "summary": ("text", "YES"),
        "rationale": ("text", "YES"),
        "strengths": ("ARRAY", "NO"),
        "gaps": ("ARRAY", "NO"),
        "error": ("text", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    }
    for column, expected_shape in expected.items():
        assert cols[column] == expected_shape


@pytest.mark.asyncio
async def test_llm_match_batch_indexes_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename IN ('llm_match_batches', 'llm_match_batch_items')
                """
            )
        )
    ).all()
    by_name = {row[0]: row[1] for row in rows}
    assert "uq_llm_match_batches_one_active_per_profile" in by_name
    assert "ix_llm_match_batches_next_poll_at" in by_name
    assert "uq_llm_match_batch_items_active_attempt" in by_name
    assert "ix_llm_match_batch_items_batch_status" in by_name
```

- [ ] **Step 2: Run the schema test to verify it fails**

Run:

```bash
uv run pytest tests/integration/test_llm_match_batch_schema.py -q
```

Expected: failure because `llm_match_batches` and `llm_match_batch_items` are missing.

- [ ] **Step 3: Add SQLModel batch models**

Create `app/models/llm_match_batch.py`:

```python
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


BATCH_STATUS_BUILDING = "building"
BATCH_STATUS_SUBMITTED = "submitted"
BATCH_STATUS_IMPORTING = "importing"
BATCH_STATUS_DONE = "done"
BATCH_STATUS_FAILED = "failed"
ACTIVE_BATCH_STATUSES = (
    BATCH_STATUS_BUILDING,
    BATCH_STATUS_SUBMITTED,
    BATCH_STATUS_IMPORTING,
)

ITEM_STATUS_SUBMITTED = "submitted"
ITEM_STATUS_RETRYABLE_FAILED = "retryable_failed"
ITEM_STATUS_TERMINAL_FAILED = "terminal_failed"
ITEM_STATUS_IMPORTED = "imported"
ACTIVE_ITEM_STATUSES = (ITEM_STATUS_SUBMITTED,)


class LLMMatchBatch(SQLModel, table=True):
    __tablename__ = "llm_match_batches"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    profile_id: uuid.UUID = Field(foreign_key="user_profiles.id")
    provider: str = Field(sa_column=Column(sa.Text, nullable=False))
    provider_batch_id: str | None = Field(default=None, sa_column=Column(sa.Text))
    model: str = Field(sa_column=Column(sa.Text, nullable=False))
    prompt_version: str = Field(sa_column=Column(sa.Text, nullable=False))
    status: str = Field(
        default=BATCH_STATUS_BUILDING,
        sa_column=Column(sa.Text, nullable=False),
    )
    submitted_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    next_poll_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    last_polled_at: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )
    last_error: str | None = Field(default=None, sa_column=Column(sa.Text))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        sa.Index(
            "uq_llm_match_batches_one_active_per_profile",
            "profile_id",
            unique=True,
            postgresql_where=sa.text("status IN ('building', 'submitted', 'importing')"),
        ),
        sa.Index("ix_llm_match_batches_next_poll_at", "next_poll_at"),
    )


class LLMMatchBatchItem(SQLModel, table=True):
    __tablename__ = "llm_match_batch_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_id: uuid.UUID = Field(foreign_key="llm_match_batches.id")
    application_id: uuid.UUID = Field(foreign_key="applications.id")
    provider_request_key: str = Field(sa_column=Column(sa.Text, nullable=False))
    request_hash: str = Field(sa_column=Column(sa.Text, nullable=False))
    status: str = Field(
        default=ITEM_STATUS_SUBMITTED,
        sa_column=Column(sa.Text, nullable=False),
    )
    score: float | None = None
    summary: str | None = Field(default=None, sa_column=Column(sa.Text))
    rationale: str | None = Field(default=None, sa_column=Column(sa.Text))
    strengths: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(sa.Text), nullable=False),
    )
    gaps: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(sa.Text), nullable=False),
    )
    error: str | None = Field(default=None, sa_column=Column(sa.Text))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(sa.DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        sa.Index(
            "uq_llm_match_batch_items_active_attempt",
            "application_id",
            "request_hash",
            unique=True,
            postgresql_where=sa.text("status = 'submitted'"),
        ),
        sa.Index("ix_llm_match_batch_items_batch_status", "batch_id", "status"),
    )
```

Modify `app/models/__init__.py`:

```python
from app.models.llm_match_batch import LLMMatchBatch, LLMMatchBatchItem  # noqa: F401
```

Add these names to `__all__`:

```python
"LLMMatchBatch",
"LLMMatchBatchItem",
```

- [ ] **Step 4: Add the Alembic migration**

Create `alembic/versions/b7c8d9e0f1a2_add_llm_match_batches.py`:

```python
"""add llm match batches

Revision ID: b7c8d9e0f1a2
Revises: e4f5a6b7c8d9
Create Date: 2026-05-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_match_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_batch_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_llm_match_batches_one_active_per_profile",
        "llm_match_batches",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('building', 'submitted', 'importing')"),
    )
    op.create_index(
        "ix_llm_match_batches_next_poll_at",
        "llm_match_batches",
        ["next_poll_at"],
    )

    op.create_table(
        "llm_match_batch_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("provider_request_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("strengths", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("gaps", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["llm_match_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_llm_match_batch_items_active_attempt",
        "llm_match_batch_items",
        ["application_id", "request_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'submitted'"),
    )
    op.create_index(
        "ix_llm_match_batch_items_batch_status",
        "llm_match_batch_items",
        ["batch_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_match_batch_items_batch_status", table_name="llm_match_batch_items")
    op.drop_index("uq_llm_match_batch_items_active_attempt", table_name="llm_match_batch_items")
    op.drop_table("llm_match_batch_items")
    op.drop_index("ix_llm_match_batches_next_poll_at", table_name="llm_match_batches")
    op.drop_index("uq_llm_match_batches_one_active_per_profile", table_name="llm_match_batches")
    op.drop_table("llm_match_batches")
```

- [ ] **Step 5: Run the schema test to verify it passes**

Run:

```bash
uv run pytest tests/integration/test_llm_match_batch_schema.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add app/models/llm_match_batch.py app/models/__init__.py alembic/versions/b7c8d9e0f1a2_add_llm_match_batches.py tests/integration/test_llm_match_batch_schema.py
git commit -m "feat: add llm match batch schema"
```

---

### Task 2: Add Batch Matching Configuration and Payloads

**Files:**
- Modify: `app/config.py`
- Modify: `app/worker/payloads.py`
- Modify: `app/worker/config.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_worker_payloads.py`
- Test: `tests/unit/test_worker_config.py`

- [ ] **Step 1: Write failing config and payload tests**

Add to `tests/unit/test_config.py`:

```python
def test_batch_matching_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    import app.config as cfg

    cfg._settings = None
    settings = cfg.get_settings()

    assert settings.batch_match_enabled is False
    assert settings.batch_match_dry_run is True
    assert settings.batch_match_provider == "fake"
    assert settings.batch_match_prompt_version == "batch-match-v1"
    assert settings.batch_match_max_apps_per_request == 10
    assert settings.batch_match_max_request_chars == 60000
    assert settings.batch_match_poll_interval_seconds == 60
    assert settings.batch_match_max_items_per_batch == 100
```

Add to `tests/unit/test_worker_payloads.py`:

```python
def test_batch_match_payload_requires_profile_id():
    import pytest
    from pydantic import ValidationError

    from app.worker.payloads import BatchMatchPayload

    with pytest.raises(ValidationError):
        BatchMatchPayload()
```

Add to `tests/unit/test_worker_config.py`:

```python
def test_worker_defaults_use_fast_and_slow_lanes(monkeypatch):
    monkeypatch.delenv("WORKER_FAST_JOB_TYPES", raising=False)
    monkeypatch.delenv("WORKER_LLM_JOB_TYPES", raising=False)
    monkeypatch.delenv("WORKER_SLOW_JOB_TYPES", raising=False)

    from app.worker.config import WorkerSettings

    settings = WorkerSettings()
    lanes = settings.lane_configs()

    assert lanes[0].name == "fast"
    assert lanes[0].job_types == ("match", "generate-cover-letter")
    assert lanes[1].name == "slow"
    assert lanes[1].job_types == ("fetch-slug", "maintenance", "batch-match")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_config.py::test_batch_matching_defaults tests/unit/test_worker_payloads.py::test_batch_match_payload_requires_profile_id tests/unit/test_worker_config.py::test_worker_defaults_use_fast_and_slow_lanes -q
```

Expected: failures for missing settings, payload, and fast lane config.

- [ ] **Step 3: Add settings**

Modify `app/config.py` inside `Settings`:

```python
    batch_match_enabled: bool = False
    batch_match_dry_run: bool = True
    batch_match_provider: str = "fake"
    batch_match_prompt_version: str = "batch-match-v1"
    batch_match_max_apps_per_request: int = 10
    batch_match_max_request_chars: int = 60000
    batch_match_poll_interval_seconds: int = 60
    batch_match_max_items_per_batch: int = 100
```

- [ ] **Step 4: Add the payload model**

Modify `app/worker/payloads.py`:

```python
class BatchMatchPayload(BaseModel):
    profile_id: uuid.UUID
```

- [ ] **Step 5: Rename worker lane settings and include `batch-match`**

Modify `app/worker/config.py`:

```python
DEFAULT_FAST_JOB_TYPES = "match,generate-cover-letter"
DEFAULT_SLOW_JOB_TYPES = "fetch-slug,maintenance,batch-match"
```

Update `WorkerSettings` fields:

```python
    fast_job_types: str | None = DEFAULT_FAST_JOB_TYPES
    fast_concurrency: int = 6
    slow_job_types: str | None = DEFAULT_SLOW_JOB_TYPES
    slow_concurrency: int = 20
```

Update `lanes_enabled` and `lane_configs()` to use `fast_job_types` and lane name `"fast"`. Keep backward compatibility for the old environment variable by accepting `llm_job_types` as an alias only if the codebase needs it; prefer tests that assert the new name.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/unit/test_config.py::test_batch_matching_defaults tests/unit/test_worker_payloads.py::test_batch_match_payload_requires_profile_id tests/unit/test_worker_config.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add app/config.py app/worker/payloads.py app/worker/config.py tests/unit/test_config.py tests/unit/test_worker_payloads.py tests/unit/test_worker_config.py
git commit -m "feat: add batch match worker configuration"
```

---

### Task 3: Add Provider Protocol and Token-Aware Packing

**Files:**
- Create: `app/services/batch_match_provider.py`
- Create: `app/services/batch_match_packing.py`
- Create: `tests/unit/test_batch_match_packing.py`

- [ ] **Step 1: Write failing packing tests**

Create `tests/unit/test_batch_match_packing.py`:

```python
import uuid

from app.services.batch_match_packing import (
    BatchJobContext,
    build_request_hash,
    pack_provider_requests,
)


def _job(index: int, description: str = "Build APIs") -> BatchJobContext:
    return BatchJobContext(
        application_id=uuid.UUID(int=index),
        title=f"Engineer {index}",
        company="Acme",
        location="Remote - United States",
        workplace_type="remote",
        description=description,
    )


def test_pack_provider_requests_caps_at_ten_apps():
    groups = pack_provider_requests(
        profile_text="Python backend engineer",
        jobs=[_job(i) for i in range(1, 12)],
        max_apps_per_request=10,
        max_request_chars=100000,
    )

    assert [len(group.jobs) for group in groups] == [10, 1]
    assert groups[0].request_key == "request-0001"
    assert groups[1].request_key == "request-0002"


def test_pack_provider_requests_respects_char_budget():
    groups = pack_provider_requests(
        profile_text="Python backend engineer",
        jobs=[_job(1, "A" * 100), _job(2, "B" * 100), _job(3, "C" * 100)],
        max_apps_per_request=10,
        max_request_chars=430,
    )

    assert [len(group.jobs) for group in groups] == [2, 1]


def test_request_hash_changes_when_context_changes():
    first = build_request_hash(
        prompt_version="batch-match-v1",
        model="gemini-2.5-flash",
        profile_text="Python",
        job=_job(1, "Build APIs"),
    )
    second = build_request_hash(
        prompt_version="batch-match-v1",
        model="gemini-2.5-flash",
        profile_text="Python",
        job=_job(1, "Build ML systems"),
    )

    assert first != second
```

- [ ] **Step 2: Run packing tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_batch_match_packing.py -q
```

Expected: import failure because packing module is missing.

- [ ] **Step 3: Add provider protocol**

Create `app/services/batch_match_provider.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderJobResult:
    application_id: str
    score: float | None
    summary: str
    rationale: str
    strengths: list[str]
    gaps: list[str]
    error: str | None = None


@dataclass(frozen=True)
class ProviderRequestResult:
    request_key: str
    results: list[ProviderJobResult]
    error: str | None = None


@dataclass(frozen=True)
class ProviderBatchStatus:
    ready: bool
    failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ProviderBatchOutput:
    requests: list[ProviderRequestResult]


class BatchMatchProvider(Protocol):
    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        raise NotImplementedError

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        raise NotImplementedError

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        raise NotImplementedError


class FakeBatchMatchProvider:
    def __init__(
        self,
        *,
        provider_batch_id: str = "fake-provider-batch",
        ready: bool = True,
        output: ProviderBatchOutput | None = None,
    ) -> None:
        self.provider_batch_id = provider_batch_id
        self.ready = ready
        self.output = output or ProviderBatchOutput(requests=[])
        self.submitted_requests: list[dict] = []

    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        self.submitted_requests = requests
        return self.provider_batch_id

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        return ProviderBatchStatus(ready=self.ready)

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        return self.output
```

- [ ] **Step 4: Add packing module**

Create `app/services/batch_match_packing.py`:

```python
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchJobContext:
    application_id: uuid.UUID
    title: str
    company: str
    location: str | None
    workplace_type: str | None
    description: str


@dataclass(frozen=True)
class PackedProviderRequest:
    request_key: str
    jobs: list[BatchJobContext]
    estimated_chars: int


def build_request_hash(
    *,
    prompt_version: str,
    model: str,
    profile_text: str,
    job: BatchJobContext,
) -> str:
    payload = {
        "prompt_version": prompt_version,
        "model": model,
        "profile_text": profile_text,
        "job": {
            "application_id": str(job.application_id),
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "workplace_type": job.workplace_type,
            "description": job.description,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def estimate_request_chars(*, profile_text: str, jobs: list[BatchJobContext]) -> int:
    fixed = 1800 + len(profile_text)
    per_job = 0
    for job in jobs:
        per_job += len(str(job.application_id))
        per_job += len(job.title)
        per_job += len(job.company)
        per_job += len(job.location or "unspecified")
        per_job += len(job.workplace_type or "unspecified")
        per_job += len(job.description)
        per_job += 320
    return fixed + per_job


def _truncate_to_budget(job: BatchJobContext, max_description_chars: int) -> BatchJobContext:
    if len(job.description) <= max_description_chars:
        return job
    return BatchJobContext(
        application_id=job.application_id,
        title=job.title,
        company=job.company,
        location=job.location,
        workplace_type=job.workplace_type,
        description=job.description[:max_description_chars] + "\n\n[Description truncated for batch]",
    )


def pack_provider_requests(
    *,
    profile_text: str,
    jobs: list[BatchJobContext],
    max_apps_per_request: int,
    max_request_chars: int,
) -> list[PackedProviderRequest]:
    groups: list[PackedProviderRequest] = []
    current: list[BatchJobContext] = []

    def flush() -> None:
        if not current:
            return
        groups.append(
            PackedProviderRequest(
                request_key=f"request-{len(groups) + 1:04d}",
                jobs=list(current),
                estimated_chars=estimate_request_chars(profile_text=profile_text, jobs=current),
            )
        )
        current.clear()

    for original_job in jobs:
        job = _truncate_to_budget(original_job, max(1000, max_request_chars // 2))
        candidate = [*current, job]
        if current and (
            len(candidate) > max_apps_per_request
            or estimate_request_chars(profile_text=profile_text, jobs=candidate)
            > max_request_chars
        ):
            flush()
        current.append(job)
    flush()
    return groups
```

- [ ] **Step 5: Run packing tests**

Run:

```bash
uv run pytest tests/unit/test_batch_match_packing.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add app/services/batch_match_provider.py app/services/batch_match_packing.py tests/unit/test_batch_match_packing.py
git commit -m "feat: add batch match provider packing"
```

---

### Task 4: Add Batch Match Service Core

**Files:**
- Create: `app/services/batch_match_service.py`
- Create: `tests/integration/test_batch_match_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/integration/test_batch_match_service.py` with focused tests for:

```python
async def test_build_submits_batch_for_profile_unscored_apps(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=3)
    provider = FakeBatchMatchProvider(ready=False)

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=provider)

    assert result.submitted == 3
    assert result.imported == 0
    assert len(provider.submitted_requests) == 1
    assert len(provider.submitted_requests[0]["jobs"]) == 3


async def test_deterministic_reject_is_not_submitted(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_non_us_app(db_session)
    provider = FakeBatchMatchProvider(ready=False)

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=provider)

    assert result.deterministic_rejected == 1
    assert result.submitted == 0
    assert provider.submitted_requests == []


async def test_import_partial_provider_output(db_session):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        ProviderBatchOutput,
        ProviderJobResult,
        ProviderRequestResult,
    )
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=2)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)

    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-1",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.8,
                            summary="Backend API role",
                            rationale="Strong Python match",
                            strengths=["Python"],
                            gaps=[],
                        ),
                        ProviderJobResult(
                            application_id=str(apps[1].id),
                            score=None,
                            summary="",
                            rationale="Provider returned null score",
                            strengths=[],
                            gaps=[],
                        ),
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 1
    assert result.retryable_failed == 1
```

Also add local helper seed functions in this test file. Use `User`, `UserProfile`, `Company`, `Job`, and `Application` in the same pattern as `tests/integration/test_handler_match.py`.

- [ ] **Step 2: Run service tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_batch_match_service.py -q
```

Expected: import failure because `batch_match_service` is missing.

- [ ] **Step 3: Implement service result and selection helpers**

Create `app/services/batch_match_service.py` with:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.llm_match_batch import (
    ACTIVE_BATCH_STATUSES,
    BATCH_STATUS_DONE,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_IMPORTING,
    BATCH_STATUS_SUBMITTED,
    ITEM_STATUS_IMPORTED,
    ITEM_STATUS_RETRYABLE_FAILED,
    ITEM_STATUS_TERMINAL_FAILED,
    LLMMatchBatch,
    LLMMatchBatchItem,
)
from app.models.user_profile import UserProfile
from app.services.batch_match_packing import BatchJobContext, build_request_hash, pack_provider_requests
from app.services.batch_match_provider import BatchMatchProvider, ProviderJobResult
from app.services.match_service import DISPLAY_JOB_MAX_AGE_DAYS, format_profile_text
from app.services.profile_service import get_skills, get_work_experiences
from app.services.remote_policy import evaluate_remote_policy, evaluate_us_location_policy
from app.worker.handlers.match import _deterministic_rejection_score


@dataclass(frozen=True)
class BatchMatchTickResult:
    selected: int = 0
    deterministic_rejected: int = 0
    submitted: int = 0
    imported: int = 0
    retryable_failed: int = 0
    terminal_failed: int = 0
    requeued: bool = False


async def run_batch_match_tick(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    provider: BatchMatchProvider,
) -> BatchMatchTickResult:
    active = await _get_active_batch(session, profile_id)
    if active is not None and active.status == BATCH_STATUS_SUBMITTED:
        return await _poll_and_import(session, batch=active, provider=provider)
    if active is not None:
        return BatchMatchTickResult(requeued=True)
    return await _build_and_submit(session, profile_id=profile_id, provider=provider)
```

Then implement `_get_active_batch`, `_build_and_submit`, `_poll_and_import`, `_apply_deterministic_reject_if_needed`, and `_mark_item_retryable`. Keep each helper under roughly 80 lines. Use the existing deterministic policy calls from `app/worker/handlers/match.py` as the behavioral source.

- [ ] **Step 4: Ensure deterministic rejects are shared**

If `_apply_deterministic_reject_if_needed` duplicates too much from `MatchHandler`, extract a helper into `app/services/match_service.py`:

```python
def deterministic_rejection_fields(profile: UserProfile, job: Job, threshold: float) -> dict | None:
    us_verdict = evaluate_us_location_policy(job)
    if us_verdict.hard_mismatch:
        gap = us_verdict.gap or "Deterministic match policy mismatch"
        return {
            "score": _deterministic_rejection_score(threshold),
            "summary": "Deterministic mismatch: non-US position",
            "rationale": gap,
            "strengths": [],
            "gaps": [gap],
        }
    remote_verdict = evaluate_remote_policy(profile, job)
    if remote_verdict.hard_mismatch:
        gap = remote_verdict.gap or "Deterministic match policy mismatch"
        return {
            "score": _deterministic_rejection_score(threshold),
            "summary": "Deterministic mismatch: recurring office attendance requirement",
            "rationale": gap,
            "strengths": [],
            "gaps": [gap],
        }
    return None
```

Update `MatchHandler` to use the helper only if that makes the batch service cleaner without changing existing tests.

- [ ] **Step 5: Run service and match tests**

Run:

```bash
uv run pytest tests/integration/test_batch_match_service.py tests/integration/test_handler_match.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add app/services/batch_match_service.py app/services/match_service.py app/worker/handlers/match.py tests/integration/test_batch_match_service.py tests/integration/test_handler_match.py
git commit -m "feat: add batch match service core"
```

---

### Task 5: Add `batch-match` Worker Handler

**Files:**
- Create: `app/worker/handlers/batch_match.py`
- Modify: `app/worker/main.py`
- Modify: `app/worker/queue_service.py`
- Create: `tests/integration/test_handler_batch_match.py`

- [ ] **Step 1: Write failing handler tests**

Create `tests/integration/test_handler_batch_match.py`:

```python
import uuid

import pytest

from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.worker.handlers import HANDLERS


def _row(profile_id: uuid.UUID) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="batch-match",
        payload={"profile_id": str(profile_id)},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="worker-1",
    )


def test_batch_match_handler_registers():
    from app.worker.handlers.batch_match import BatchMatchHandler

    assert isinstance(HANDLERS["batch-match"], BatchMatchHandler)


@pytest.mark.asyncio
async def test_batch_match_handler_calls_service(db_session, monkeypatch):
    from app.services.batch_match_service import BatchMatchTickResult
    from app.worker.handlers.batch_match import BatchMatchHandler

    profile_id = uuid.uuid4()
    called = {}

    async def fake_run_batch_match_tick(session, *, profile_id, provider):
        called["profile_id"] = profile_id
        return BatchMatchTickResult(selected=0)

    monkeypatch.setattr(
        "app.worker.handlers.batch_match.run_batch_match_tick",
        fake_run_batch_match_tick,
    )

    handler = BatchMatchHandler()
    await handler(db_session, _row(profile_id))

    assert called["profile_id"] == profile_id
```

- [ ] **Step 2: Run handler tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_handler_batch_match.py -q
```

Expected: import failure because the handler module is missing.

- [ ] **Step 3: Add handler module**

Create `app/worker/handlers/batch_match.py`:

```python
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.batch_match_provider import FakeBatchMatchProvider
from app.services.batch_match_service import run_batch_match_tick
from app.worker.handlers import HANDLERS
from app.worker.payloads import BatchMatchPayload

log = structlog.get_logger()


class BatchMatchHandler:
    max_attempts = 5

    async def __call__(self, session: AsyncSession, row) -> None:
        payload = BatchMatchPayload(**row.payload)
        provider = FakeBatchMatchProvider(ready=False)
        result = await run_batch_match_tick(
            session,
            profile_id=payload.profile_id,
            provider=provider,
        )
        await log.ainfo(
            "worker.batch_match.done",
            profile_id=str(payload.profile_id),
            selected=result.selected,
            deterministic_rejected=result.deterministic_rejected,
            submitted=result.submitted,
            imported=result.imported,
            retryable_failed=result.retryable_failed,
            terminal_failed=result.terminal_failed,
            requeued=result.requeued,
        )


HANDLERS["batch-match"] = BatchMatchHandler()
```

In a later task, replace direct `FakeBatchMatchProvider` construction with a provider factory that honors settings.

- [ ] **Step 4: Import handler in worker main**

Modify the handler imports in `app/worker/main.py`:

```python
    batch_match,
    fetch_slug,
    generate_cover_letter,
    maintenance,
    match,
```

- [ ] **Step 5: Prioritize `batch-match`**

Modify `app/worker/queue_service.py` ordering:

```sql
WHEN 'generate-cover-letter' THEN 0
WHEN 'fetch-slug' THEN 1
WHEN 'maintenance' THEN 2
WHEN 'batch-match' THEN 3
WHEN 'match' THEN 4
ELSE 5
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/integration/test_handler_batch_match.py tests/unit/test_worker_config.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add app/worker/handlers/batch_match.py app/worker/main.py app/worker/queue_service.py tests/integration/test_handler_batch_match.py
git commit -m "feat: add batch match worker handler"
```

---

### Task 6: Wire `fetch-slug -> batch-match`

**Files:**
- Modify: `app/scheduler/tasks.py`
- Create: `tests/integration/test_fetch_slug_batch_match.py`

- [ ] **Step 1: Write failing fetch contract test**

Create `tests/integration/test_fetch_slug_batch_match.py`:

```python
import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import col, select

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue
from app.scheduler.tasks import _enqueue_batch_match_for_affected_profiles


@pytest.mark.asyncio
async def test_enqueue_batch_match_for_affected_profiles(db_session, monkeypatch):
    monkeypatch.setenv("BATCH_MATCH_ENABLED", "true")
    import app.config as cfg

    cfg._settings = None

    user = User(id=uuid.uuid4(), email="batch-fetch@test.com")
    db_session.add(user)
    company = Company(
        canonical_name="Acme",
        normalized_key=f"acme-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "acme"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[company.id],
        search_active=True,
    )
    db_session.add(profile)
    job = Job(
        source="greenhouse",
        external_id=f"job-{uuid.uuid4()}",
        title="Backend Engineer",
        company_name="Acme",
        company_id=company.id,
        apply_url="https://example.com/job",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(profile)
    await db_session.refresh(job)

    app = Application(job_id=job.id, profile_id=profile.id, match_strengths=[], match_gaps=[])
    db_session.add(app)
    await db_session.commit()

    count = await _enqueue_batch_match_for_affected_profiles(job.id, db_session)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(WorkQueue).where(
                WorkQueue.job_type == "batch-match",
                col(WorkQueue.dedupe_key) == f"batch-match:{profile.id}",
            )
        )
    ).scalars().all()
    assert count == 1
    assert len(rows) == 1
    assert rows[0].payload == {"profile_id": str(profile.id)}
```

- [ ] **Step 2: Run fetch contract test to verify it fails**

Run:

```bash
uv run pytest tests/integration/test_fetch_slug_batch_match.py -q
```

Expected: import failure for `_enqueue_batch_match_for_affected_profiles`.

- [ ] **Step 3: Add helper and feature-flagged enqueue**

Modify `app/scheduler/tasks.py`:

```python
async def _enqueue_batch_match_for_affected_profiles(job_id, session) -> int:
    from app.config import get_settings
    from app.models.application import Application
    from app.worker.queue_service import enqueue

    settings = get_settings()
    if not settings.batch_match_enabled:
        return 0

    rows = (
        await session.execute(
            select(Application.profile_id)
            .where(
                Application.job_id == job_id,
                col(Application.match_score).is_(None),
                col(Application.status).in_(("pending_review", "auto_rejected")),
            )
            .distinct()
        )
    ).all()
    count = 0
    for row in rows:
        profile_id = row[0]
        queued = await enqueue(
            session,
            job_type="batch-match",
            payload={"profile_id": str(profile_id)},
            dedupe_key=f"batch-match:{profile_id}",
            on_conflict="upsert_reset_not_before",
        )
        if queued is not None:
            count += 1
    return count
```

In `fetch_one_slug`, replace unconditional per-application `match` enqueueing with:

```python
batch_profiles = await _enqueue_batch_match_for_affected_profiles(job.id, session)
if batch_profiles:
    matches_enqueued += batch_profiles
    continue
```

Keep the existing `match` enqueue path when `batch_match_enabled` is false.

- [ ] **Step 4: Run fetch tests**

Run:

```bash
uv run pytest tests/integration/test_fetch_slug_batch_match.py tests/integration/test_handler_fetch_slug.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/scheduler/tasks.py tests/integration/test_fetch_slug_batch_match.py
git commit -m "feat: enqueue batch match from fetch slug"
```

---

### Task 7: Add Sync Status Compatibility

**Files:**
- Modify: `app/api/jobs.py`
- Modify: `tests/integration/test_sync_status_endpoint.py`

- [ ] **Step 1: Write failing sync status test**

Add to `tests/integration/test_sync_status_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_sync_status_matching_with_active_batch(client, db_session, auth_headers, seeded_user):
    from datetime import UTC, datetime
    from app.models.llm_match_batch import LLMMatchBatch, BATCH_STATUS_SUBMITTED

    user, profile = seeded_user
    batch = LLMMatchBatch(
        profile_id=profile.id,
        provider="fake",
        provider_batch_id="provider-1",
        model="gemini-2.5-flash",
        prompt_version="batch-match-v1",
        status=BATCH_STATUS_SUBMITTED,
        submitted_at=datetime.now(UTC),
    )
    db_session.add(batch)
    await db_session.commit()

    response = await client.get("/api/jobs/sync/status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["state"] == "matching"
```

- [ ] **Step 2: Run sync status test to verify it fails**

Run:

```bash
uv run pytest tests/integration/test_sync_status_endpoint.py::test_sync_status_matching_with_active_batch -q
```

Expected: state is `"idle"` because active batches are not counted.

- [ ] **Step 3: Count active batch work**

Modify `app/api/jobs.py` near the existing `matches_pending` query:

```python
    batch_queue_pending = int(
        (
            await session.execute(
                select(func.count())
                .select_from(WorkQueue)
                .where(
                    WorkQueue.job_type == "batch-match",
                    col(WorkQueue.status).in_(("pending", "in_progress")),
                    col(WorkQueue.payload)["profile_id"].astext == str(profile.id),
                )
            )
        ).scalar_one()
    )

    active_batches = int(
        (
            await session.execute(
                select(func.count())
                .select_from(LLMMatchBatch)
                .where(
                    LLMMatchBatch.profile_id == profile.id,
                    col(LLMMatchBatch.status).in_(("building", "submitted", "importing")),
                )
            )
        ).scalar_one()
    )
```

Import `LLMMatchBatch`. Change state logic:

```python
    elif matches_pending > 0 or batch_queue_pending > 0 or active_batches > 0:
        state = "matching"
```

Return optional compatibility counts without removing existing keys:

```python
        "batch_matches_pending": batch_queue_pending + active_batches,
```

- [ ] **Step 4: Run sync status tests**

Run:

```bash
uv run pytest tests/integration/test_sync_status_endpoint.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/api/jobs.py tests/integration/test_sync_status_endpoint.py
git commit -m "feat: show batch matching in sync status"
```

---

### Task 8: Add Provider Factory and Dry-Run Behavior

**Files:**
- Modify: `app/services/batch_match_provider.py`
- Modify: `app/worker/handlers/batch_match.py`
- Create: `tests/unit/test_batch_match_provider.py`

- [ ] **Step 1: Write failing provider factory tests**

Create `tests/unit/test_batch_match_provider.py`:

```python
def test_get_batch_match_provider_uses_fake_in_test(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "test")
    import app.config as cfg

    cfg._settings = None

    from app.services.batch_match_provider import FakeBatchMatchProvider, get_batch_match_provider

    provider = get_batch_match_provider()

    assert isinstance(provider, FakeBatchMatchProvider)


def test_get_batch_match_provider_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("BATCH_MATCH_PROVIDER", "unknown")
    import app.config as cfg

    cfg._settings = None

    from app.services.batch_match_provider import get_batch_match_provider

    try:
        get_batch_match_provider()
    except ValueError as exc:
        assert "unknown batch match provider" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run provider factory tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_batch_match_provider.py -q
```

Expected: failure because `get_batch_match_provider` is missing.

- [ ] **Step 3: Add provider factory**

Modify `app/services/batch_match_provider.py`:

```python
def get_batch_match_provider() -> BatchMatchProvider:
    from app.config import get_settings

    settings = get_settings()
    if settings.environment == "test" or settings.batch_match_provider == "fake":
        return FakeBatchMatchProvider(ready=False)
    if settings.batch_match_provider == "gemini":
        raise ValueError("gemini batch match provider is not implemented")
    raise ValueError(f"unknown batch match provider: {settings.batch_match_provider}")
```

Modify `app/worker/handlers/batch_match.py` to call `get_batch_match_provider()`.

- [ ] **Step 4: Run provider and handler tests**

Run:

```bash
uv run pytest tests/unit/test_batch_match_provider.py tests/integration/test_handler_batch_match.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/services/batch_match_provider.py app/worker/handlers/batch_match.py tests/unit/test_batch_match_provider.py
git commit -m "feat: add batch match provider factory"
```

---

### Task 9: Add Real Provider Adapter Skeleton

**Files:**
- Create: `app/services/gemini_batch_match_provider.py`
- Modify: `app/services/batch_match_provider.py`
- Create: `tests/unit/test_gemini_batch_match_provider.py`

- [ ] **Step 1: Write adapter construction test**

Create `tests/unit/test_gemini_batch_match_provider.py`:

```python
def test_gemini_provider_builds_request_payload():
    from app.services.gemini_batch_match_provider import build_gemini_batch_request

    payload = build_gemini_batch_request(
        request_key="request-0001",
        profile_text="Python backend engineer",
        jobs=[
            {
                "application_id": "00000000-0000-0000-0000-000000000001",
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Remote - United States",
                "workplace_type": "remote",
                "description": "Build APIs",
            }
        ],
    )

    assert payload["key"] == "request-0001"
    assert "Python backend engineer" in payload["request"]["contents"][0]["parts"][0]["text"]
    assert "00000000-0000-0000-0000-000000000001" in payload["request"]["contents"][0]["parts"][0]["text"]
```

- [ ] **Step 2: Run adapter test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_gemini_batch_match_provider.py -q
```

Expected: import failure because the adapter module is missing.

- [ ] **Step 3: Add adapter payload builder and explicit runtime guard**

Create `app/services/gemini_batch_match_provider.py`:

```python
from __future__ import annotations

from app.agents.matching_agent import SCORING_SYSTEM_PROMPT


def build_gemini_batch_request(
    *,
    request_key: str,
    profile_text: str,
    jobs: list[dict],
) -> dict:
    prompt = (
        f"{SCORING_SYSTEM_PROMPT}\n\n"
        "Return JSON with a top-level results array. Return exactly one result per application_id.\n\n"
        f"PROFILE:\n{profile_text}\n\n"
        f"JOBS:\n{jobs}"
    )
    return {
        "key": request_key,
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        },
    }


class GeminiBatchMatchProvider:
    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        raise RuntimeError("Gemini batch submission requires provider API wiring")

    async def poll(self, *, provider_batch_id: str):
        raise RuntimeError("Gemini batch polling requires provider API wiring")

    async def fetch_output(self, *, provider_batch_id: str):
        raise RuntimeError("Gemini batch output import requires provider API wiring")
```

Modify `get_batch_match_provider()` so `batch_match_provider == "gemini"` returns `GeminiBatchMatchProvider()`. This keeps the interface ready while the first rollout can stay on fake/dry-run.

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run pytest tests/unit/test_gemini_batch_match_provider.py tests/unit/test_batch_match_provider.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/services/gemini_batch_match_provider.py app/services/batch_match_provider.py tests/unit/test_gemini_batch_match_provider.py
git commit -m "feat: add gemini batch provider skeleton"
```

---

### Task 10: Final Verification

**Files:**
- Review: `docs/superpowers/specs/2026-05-27-llm-batch-matching-design.md`
- Review: all files changed by Tasks 1-9

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest \
  tests/unit/test_batch_match_packing.py \
  tests/unit/test_batch_match_provider.py \
  tests/unit/test_gemini_batch_match_provider.py \
  tests/unit/test_worker_payloads.py \
  tests/unit/test_worker_config.py \
  tests/integration/test_llm_match_batch_schema.py \
  tests/integration/test_batch_match_service.py \
  tests/integration/test_handler_batch_match.py \
  tests/integration/test_fetch_slug_batch_match.py \
  tests/integration/test_sync_status_endpoint.py \
  tests/integration/test_handler_match.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run lint/type-style checks used by the repo**

Run:

```bash
uv run ruff check app tests
```

Expected: pass.

- [ ] **Step 3: Run full test suite if runtime is acceptable**

Run:

```bash
uv run pytest -q
```

Expected: pass.

- [ ] **Step 4: Confirm migration head and working tree**

Run:

```bash
uv run alembic heads
git status --short
```

Expected: one Alembic head containing `b7c8d9e0f1a2`; git status has no unstaged or uncommitted changes.

- [ ] **Step 5: Commit verification-only fixes if needed**

If verification required small fixes, commit them:

```bash
git add app tests alembic
git commit -m "test: verify batch matching workflow"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review Notes

- Spec coverage: schema, `fetch-slug -> batch-match`, profile-level payload, one active batch per profile, max-10 packing, token budget, deterministic rejects, item-based import, sync status compatibility, worker lane naming, fallback `match`, and rollout flags are covered by tasks.
- Placeholder scan: this plan avoids forbidden placeholder language; Task 9 intentionally creates a guarded Gemini skeleton so production traffic cannot accidentally call an incomplete adapter.
- Type consistency: `profile_id`, `provider_request_key`, `request_hash`, `batch-match`, `fast`, and status strings match the approved spec.
