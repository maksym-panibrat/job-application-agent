# Feedback Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build authenticated in-app feedback reporting with durable database storage, conservative diagnostics, and best-effort generic webhook notifications.

**Architecture:** Add a focused backend feedback feature with a `feedback_reports` table, validation/sanitization helpers, a service layer that owns persistence plus webhook dispatch, and a `POST /api/feedback` route. Add a frontend modal opened from the authenticated app shell, collecting category/message and sending bounded page diagnostics through the existing API client and toast patterns.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async sessions, Alembic, httpx, Pydantic, React, TypeScript, Vitest, Testing Library, MSW, Tailwind UI primitives already in `frontend/src/components/ui`.

---

## File Structure

Backend files:
- Create `app/models/feedback_report.py`: SQLModel table for feedback records.
- Modify `app/models/__init__.py`: import/export `FeedbackReport`.
- Create `alembic/versions/e4f5a6b7c8d9_add_feedback_reports.py`: migration for `feedback_reports`.
- Modify `app/config.py`: add webhook URL and timeout settings.
- Create `app/services/feedback_service.py`: category constants, diagnostics sanitizer, report creation, webhook dispatch.
- Create `app/api/feedback.py`: request/response models and authenticated route.
- Modify `app/main.py`: include feedback router.
- Create `tests/integration/test_feedback_api.py`: endpoint, persistence, diagnostics, and webhook behavior.

Frontend files:
- Modify `frontend/src/api/client.ts`: types and `submitFeedback`.
- Create `frontend/src/components/FeedbackModal.tsx`: form UI, diagnostics collection, submit behavior.
- Create `frontend/src/components/FeedbackModal.test.tsx`: modal validation/submission tests.
- Modify `frontend/src/components/AppShell.tsx`: feedback entry points in desktop header and mobile menu.
- Modify `frontend/src/components/AppShell.test.tsx`: entry-point behavior tests.
- Modify `frontend/src/components/ui/icons/index.ts`: export the chosen feedback icon if a new icon component is added.
- Create `frontend/src/components/ui/icons/Feedback.tsx`: simple line icon matching local icon style.

Keep the backend service independent from FastAPI route code so diagnostics sanitization and webhook behavior can be tested directly through the endpoint and reused later by agents/operator tooling.

---

### Task 1: Backend model and migration

**Files:**
- Create: `app/models/feedback_report.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/e4f5a6b7c8d9_add_feedback_reports.py`
- Test: `tests/integration/test_feedback_api.py`

- [ ] **Step 1: Write the first failing persistence test**

Create `tests/integration/test_feedback_api.py` with this initial test:

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_feedback_submit_creates_row(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    body = {
        "category": "feature_request",
        "message": "Please let me hide companies I rejected.",
        "diagnostics": {
            "reported_at_client": "2026-05-25T20:15:00.000Z",
            "path": "/matches?status=pending",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 1440, "height": 900},
            "timezone": "America/Los_Angeles",
            "route_context": {},
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] is True
    assert payload["notification_status"] == "not_configured"

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    user, _profile = seeded_user
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(FeedbackReport).where(FeedbackReport.user_id == user.id)
            )
        ).scalar_one()

    assert row.user_email == user.email
    assert row.category == "feature_request"
    assert row.message == "Please let me hide companies I rejected."
    assert row.notification_status == "not_configured"
    assert row.notification_error is None
    assert row.diagnostics["path"] == "/matches?status=pending"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/integration/test_feedback_api.py::test_feedback_submit_creates_row -q
```

Expected: FAIL because `/api/feedback` and `FeedbackReport` do not exist yet.

- [ ] **Step 3: Add the SQLModel table**

Create `app/models/feedback_report.py`:

```python
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class FeedbackReport(SQLModel, table=True):
    __tablename__ = "feedback_reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        sa_column=Column(ForeignKey("users.id"), index=True, nullable=False),
    )
    user_email: str = Field(index=True, max_length=320)
    category: str = Field(index=True, max_length=32)
    message: str = Field(sa_column=Column(Text, nullable=False))
    diagnostics: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    notification_status: str = Field(index=True, max_length=32)
    notification_error: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
```

- [ ] **Step 4: Register the model**

Modify `app/models/__init__.py`:

```python
from app.models.feedback_report import FeedbackReport  # noqa: F401
```

Add `"FeedbackReport"` to `__all__`.

- [ ] **Step 5: Add the Alembic migration**

Create `alembic/versions/e4f5a6b7c8d9_add_feedback_reports.py`:

```python
"""add feedback reports

Revision ID: e4f5a6b7c8d9
Revises: 05b608a37f60, 5a6b7c8d9e0f
Create Date: 2026-05-25 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: tuple[str, str] = ("05b608a37f60", "5a6b7c8d9e0f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_email", sqlmodel.sql.sqltypes.AutoString(length=320), nullable=False),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "notification_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "notification_error",
            sqlmodel.sql.sqltypes.AutoString(length=512),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_reports_user_id"), "feedback_reports", ["user_id"])
    op.create_index(op.f("ix_feedback_reports_user_email"), "feedback_reports", ["user_email"])
    op.create_index(op.f("ix_feedback_reports_category"), "feedback_reports", ["category"])
    op.create_index(
        op.f("ix_feedback_reports_notification_status"),
        "feedback_reports",
        ["notification_status"],
    )
    op.create_index(op.f("ix_feedback_reports_created_at"), "feedback_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_reports_created_at"), table_name="feedback_reports")
    op.drop_index(
        op.f("ix_feedback_reports_notification_status"),
        table_name="feedback_reports",
    )
    op.drop_index(op.f("ix_feedback_reports_category"), table_name="feedback_reports")
    op.drop_index(op.f("ix_feedback_reports_user_email"), table_name="feedback_reports")
    op.drop_index(op.f("ix_feedback_reports_user_id"), table_name="feedback_reports")
    op.drop_table("feedback_reports")
```

This repository currently has two Alembic heads, `05b608a37f60` and `5a6b7c8d9e0f`. This migration intentionally depends on both heads so it does not create a third head.

Run `uv run alembic heads` before committing. Expected output includes only `e4f5a6b7c8d9 (head)` after this migration is present.

- [ ] **Step 6: Run the persistence test again**

Run:

```bash
pytest tests/integration/test_feedback_api.py::test_feedback_submit_creates_row -q
```

Expected: still FAIL because the API route/service is not implemented. The model import error should be gone.

- [ ] **Step 7: Commit model and migration**

```bash
git add app/models/feedback_report.py app/models/__init__.py alembic/versions/e4f5a6b7c8d9_add_feedback_reports.py tests/integration/test_feedback_api.py
git commit -m "feat: add feedback report model"
```

---

### Task 2: Backend service, API route, and validation

**Files:**
- Create: `app/services/feedback_service.py`
- Create: `app/api/feedback.py`
- Modify: `app/main.py`
- Modify: `app/config.py`
- Modify: `tests/integration/test_feedback_api.py`

- [ ] **Step 1: Extend backend tests for validation and diagnostics**

Append these tests to `tests/integration/test_feedback_api.py`:

```python
@pytest.mark.asyncio
async def test_feedback_rejects_invalid_category(auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "confusing", "message": "bad category", "diagnostics": {}},
            headers=auth_headers,
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_feedback_rejects_empty_message(auth_headers, seeded_user):
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "   ", "diagnostics": {}},
            headers=auth_headers,
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_feedback_requires_auth():
    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "Cannot submit", "diagnostics": {}},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_feedback_sanitizes_diagnostics(db_session, auth_headers, seeded_user):
    from app.main import app as fastapi_app

    body = {
        "category": "bug",
        "message": "The match page looked wrong.",
        "diagnostics": {
            "reported_at_client": "x" * 100,
            "path": "/matches/abc?chat=1",
            "page_title": "Job Search",
            "user_agent": "Browser/1.0",
            "viewport": {"width": 390, "height": 844, "ignored": "drop me"},
            "timezone": "America/Los_Angeles",
            "route_context": {"application_id": "abc", "too_long": "y" * 300},
            "page_content": "must be dropped",
        },
    }
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/feedback", json=body, headers=auth_headers)

    assert response.status_code == 200

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    async with get_session_factory()() as session:
        row = (await session.execute(select(FeedbackReport))).scalar_one()

    assert set(row.diagnostics) == {
        "reported_at_client",
        "path",
        "page_title",
        "user_agent",
        "viewport",
        "timezone",
        "route_context",
    }
    assert len(row.diagnostics["reported_at_client"]) == 64
    assert row.diagnostics["viewport"] == {"width": 390, "height": 844}
    assert row.diagnostics["route_context"]["application_id"] == "abc"
    assert len(row.diagnostics["route_context"]["too_long"]) == 128
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/integration/test_feedback_api.py -q
```

Expected: FAIL because route/service are missing.

- [ ] **Step 3: Add settings**

Modify `app/config.py`:

```python
    feedback_webhook_url: SecretStr | None = None
    feedback_webhook_timeout_seconds: float = 3.0
```

Place these near other operational settings such as `log_level`.

- [ ] **Step 4: Implement feedback service**

Create `app/services/feedback_service.py`:

```python
import json
from uuid import UUID

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.feedback_report import FeedbackReport
from app.models.user import User

log = structlog.get_logger()

CATEGORY_FEATURE_REQUEST = "feature_request"
CATEGORY_BUG = "bug"
CATEGORY_OTHER = "other"
ALLOWED_CATEGORIES = {CATEGORY_FEATURE_REQUEST, CATEGORY_BUG, CATEGORY_OTHER}

STATUS_PENDING = "pending"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

MAX_MESSAGE_LENGTH = 5000
MAX_DIAGNOSTICS_BYTES = 16 * 1024
MAX_NOTIFICATION_ERROR_LENGTH = 512
MESSAGE_PREVIEW_LENGTH = 240

_STRING_LIMITS = {
    "reported_at_client": 64,
    "path": 512,
    "page_title": 256,
    "user_agent": 512,
    "timezone": 128,
}


class FeedbackValidationError(ValueError):
    pass


def validate_category(category: str) -> str:
    if category not in ALLOWED_CATEGORIES:
        raise FeedbackValidationError("Invalid feedback category")
    return category


def validate_message(message: str) -> str:
    stripped = message.strip()
    if not stripped:
        raise FeedbackValidationError("Feedback message is required")
    if len(stripped) > MAX_MESSAGE_LENGTH:
        raise FeedbackValidationError("Feedback message is too long")
    return stripped


def _bounded_string(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


def sanitize_diagnostics(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FeedbackValidationError("Diagnostics must be an object")

    sanitized: dict[str, object] = {}
    for key, limit in _STRING_LIMITS.items():
        bounded = _bounded_string(value.get(key), limit)
        if bounded is not None:
            sanitized[key] = bounded

    viewport = value.get("viewport")
    if isinstance(viewport, dict):
        width = viewport.get("width")
        height = viewport.get("height")
        if isinstance(width, int) and isinstance(height, int):
            sanitized["viewport"] = {"width": width, "height": height}

    route_context = value.get("route_context")
    if isinstance(route_context, dict):
        clean_context: dict[str, str] = {}
        for raw_key, raw_val in list(route_context.items())[:64]:
            if isinstance(raw_key, str) and isinstance(raw_val, str):
                clean_context[raw_key[:128]] = raw_val[:128]
        sanitized["route_context"] = clean_context

    encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_DIAGNOSTICS_BYTES:
        raise FeedbackValidationError("Diagnostics are too large")
    return sanitized


def build_webhook_payload(report: FeedbackReport) -> dict:
    return {
        "event": "feedback.submitted",
        "feedback_id": str(report.id),
        "category": report.category,
        "message_preview": report.message[:MESSAGE_PREVIEW_LENGTH],
        "user_id": str(report.user_id),
        "user_email": report.user_email,
        "path": (report.diagnostics or {}).get("path"),
        "created_at": report.created_at.isoformat(),
        "diagnostics": report.diagnostics or {},
    }


async def create_feedback_report(
    *,
    session: AsyncSession,
    user: User,
    settings: Settings,
    category: str,
    message: str,
    diagnostics: object,
) -> FeedbackReport:
    clean_category = validate_category(category)
    clean_message = validate_message(message)
    clean_diagnostics = sanitize_diagnostics(diagnostics)
    initial_status = STATUS_PENDING if settings.feedback_webhook_url else STATUS_NOT_CONFIGURED

    report = FeedbackReport(
        user_id=user.id,
        user_email=user.email,
        category=clean_category,
        message=clean_message,
        diagnostics=clean_diagnostics,
        notification_status=initial_status,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    await log.ainfo(
        "feedback.submitted",
        feedback_id=str(report.id),
        user_id=str(user.id),
        category=report.category,
    )

    if settings.feedback_webhook_url:
        await dispatch_feedback_webhook(session, report, settings)

    return report


async def dispatch_feedback_webhook(
    session: AsyncSession,
    report: FeedbackReport,
    settings: Settings,
) -> None:
    webhook_url = settings.feedback_webhook_url
    if webhook_url is None:
        return

    try:
        async with httpx.AsyncClient(timeout=settings.feedback_webhook_timeout_seconds) as client:
            response = await client.post(
                webhook_url.get_secret_value(),
                json=build_webhook_payload(report),
            )
            response.raise_for_status()
        report.notification_status = STATUS_SENT
        report.notification_error = None
        await log.ainfo("feedback.notification_sent", feedback_id=str(report.id))
    except Exception as exc:
        report.notification_status = STATUS_FAILED
        report.notification_error = str(exc)[:MAX_NOTIFICATION_ERROR_LENGTH]
        await log.awarning(
            "feedback.notification_failed",
            feedback_id=str(report.id),
            error_type=type(exc).__name__,
        )

    try:
        session.add(report)
        await session.commit()
        await session.refresh(report)
    except Exception as exc:
        await session.rollback()
        await log.aerror(
            "feedback.notification_status_update_failed",
            feedback_id=str(report.id),
            error_type=type(exc).__name__,
        )
```

- [ ] **Step 5: Add API route**

Create `app/api/feedback.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.services.feedback_service import (
    FeedbackValidationError,
    create_feedback_report,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    category: str
    message: str = Field(min_length=1, max_length=5000)
    diagnostics: dict | None = None


class FeedbackOut(BaseModel):
    id: UUID
    created: bool
    notification_status: str


@router.post("", response_model=FeedbackOut)
async def submit_feedback(
    body: FeedbackIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FeedbackOut:
    try:
        report = await create_feedback_report(
            session=session,
            user=user,
            settings=settings,
            category=body.category,
            message=body.message,
            diagnostics=body.diagnostics,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return FeedbackOut(
        id=report.id,
        created=True,
        notification_status=report.notification_status,
    )
```

- [ ] **Step 6: Mount the router**

Modify `app/main.py`:

```python
from app.api.feedback import router as feedback_router
```

Add near the other `include_router` calls:

```python
app.include_router(feedback_router)
```

- [ ] **Step 7: Run backend tests**

Run:

```bash
pytest tests/integration/test_feedback_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit backend API**

```bash
git add app/config.py app/services/feedback_service.py app/api/feedback.py app/main.py tests/integration/test_feedback_api.py
git commit -m "feat: add feedback submission API"
```

---

### Task 3: Webhook behavior tests

**Files:**
- Modify: `tests/integration/test_feedback_api.py`
- Modify: `app/services/feedback_service.py` if tests expose issues

- [ ] **Step 1: Add webhook tests using monkeypatch**

Append to `tests/integration/test_feedback_api.py`:

```python
@pytest.mark.asyncio
async def test_feedback_webhook_success_records_sent(
    db_session, auth_headers, seeded_user, monkeypatch
):
    import app.config as cfg

    monkeypatch.setenv("FEEDBACK_WEBHOOK_URL", "https://example.test/feedback")
    monkeypatch.setattr(cfg, "_settings", None)

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            calls.append({"url": url, "json": json, "timeout": self.timeout})
            return FakeResponse()

    import app.services.feedback_service as svc

    monkeypatch.setattr(svc.httpx, "AsyncClient", FakeAsyncClient)

    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "Broken page", "diagnostics": {"path": "/"}},
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["notification_status"] == "sent"
    assert calls[0]["url"] == "https://example.test/feedback"
    assert calls[0]["json"]["event"] == "feedback.submitted"
    assert calls[0]["json"]["message_preview"] == "Broken page"
    assert "message" not in calls[0]["json"]


@pytest.mark.asyncio
async def test_feedback_webhook_failure_records_failed(
    db_session, auth_headers, seeded_user, monkeypatch
):
    import app.config as cfg

    monkeypatch.setenv("FEEDBACK_WEBHOOK_URL", "https://example.test/feedback")
    monkeypatch.setattr(cfg, "_settings", None)

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            raise RuntimeError("receiver down")

    import app.services.feedback_service as svc

    monkeypatch.setattr(svc.httpx, "AsyncClient", FakeAsyncClient)

    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/feedback",
            json={"category": "bug", "message": "Broken page", "diagnostics": {"path": "/"}},
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert response.json()["notification_status"] == "failed"

    from sqlmodel import select

    from app.database import get_session_factory
    from app.models.feedback_report import FeedbackReport

    async with get_session_factory()() as session:
        row = (await session.execute(select(FeedbackReport))).scalar_one()

    assert row.notification_status == "failed"
    assert "receiver down" in (row.notification_error or "")
```

- [ ] **Step 2: Run webhook tests**

Run:

```bash
pytest tests/integration/test_feedback_api.py::test_feedback_webhook_success_records_sent tests/integration/test_feedback_api.py::test_feedback_webhook_failure_records_failed -q
```

Expected: PASS. If either test fails because settings are cached after importing `app.main`, move the `from app.main import app as fastapi_app` import after the monkeypatch setup as shown above and reset `app.config._settings`.

- [ ] **Step 3: Run all feedback backend tests**

Run:

```bash
pytest tests/integration/test_feedback_api.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit webhook tests/fixes**

```bash
git add tests/integration/test_feedback_api.py app/services/feedback_service.py
git commit -m "test: cover feedback webhook dispatch"
```

---

### Task 4: Frontend API client and feedback modal

**Files:**
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/components/FeedbackModal.tsx`
- Create: `frontend/src/components/FeedbackModal.test.tsx`

- [ ] **Step 1: Add modal tests first**

Create `frontend/src/components/FeedbackModal.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { ToastProvider } from './ui/Toast'
import { FeedbackModal } from './FeedbackModal'

function renderModal(open = true, onClose = vi.fn()) {
  return {
    onClose,
    ...render(
      <ToastProvider>
        <FeedbackModal open={open} onClose={onClose} />
      </ToastProvider>,
    ),
  }
}

describe('FeedbackModal', () => {
  beforeEach(() => {
    sessionStorage.setItem('access_token', 'test-token')
    window.history.pushState({}, '', '/matches/app-123?chat=1')
    Object.defineProperty(document, 'title', {
      value: 'Job Search',
      configurable: true,
    })
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 844, configurable: true })
  })

  it('renders categories in the approved order', () => {
    renderModal()
    const radios = screen.getAllByRole('radio')
    expect(radios.map((radio) => radio.getAttribute('aria-label'))).toEqual([
      'Feature request',
      'Bug',
      'Other',
    ])
    expect(screen.getByRole('radio', { name: 'Feature request' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  it('does not submit an empty message', async () => {
    let posted = false
    server.use(
      http.post('/api/feedback', () => {
        posted = true
        return HttpResponse.json({ id: 'f-1', created: true, notification_status: 'sent' })
      }),
    )
    const user = userEvent.setup()
    renderModal()

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(posted).toBe(false)
  })

  it('submits category, message, and diagnostics then closes on success', async () => {
    let requestBody: any = null
    server.use(
      http.post('/api/feedback', async ({ request }) => {
        requestBody = await request.json()
        return HttpResponse.json({ id: 'f-1', created: true, notification_status: 'failed' })
      }),
    )
    const user = userEvent.setup()
    const { onClose } = renderModal()

    await user.click(screen.getByRole('radio', { name: 'Bug' }))
    await user.type(screen.getByLabelText('What happened?'), 'The page broke.')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(requestBody.category).toBe('bug')
    expect(requestBody.message).toBe('The page broke.')
    expect(requestBody.diagnostics.path).toBe('/matches/app-123?chat=1')
    expect(requestBody.diagnostics.page_title).toBe('Job Search')
    expect(requestBody.diagnostics.viewport).toEqual({ width: 390, height: 844 })
    expect(requestBody.diagnostics.route_context).toEqual({ application_id: 'app-123' })
    expect(screen.getByRole('status')).toHaveTextContent('Feedback sent')
  })

  it('keeps text and shows an error when the API fails', async () => {
    server.use(
      http.post('/api/feedback', () =>
        HttpResponse.json({ detail: 'bad' }, { status: 500 }),
      ),
    )
    const user = userEvent.setup()
    const { onClose } = renderModal()

    await user.type(screen.getByLabelText('What happened?'), 'Still here')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/could not/i))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByLabelText('What happened?')).toHaveValue('Still here')
  })
})
```

- [ ] **Step 2: Run modal tests to verify they fail**

Run:

```bash
cd frontend && npm run test -- src/components/FeedbackModal.test.tsx
```

Expected: FAIL because `FeedbackModal` and `api.submitFeedback` do not exist.

- [ ] **Step 3: Add API client types and method**

Modify `frontend/src/api/client.ts`:

```ts
export type FeedbackCategory = 'feature_request' | 'bug' | 'other'

export interface FeedbackDiagnostics {
  reported_at_client?: string
  path?: string
  page_title?: string
  user_agent?: string
  viewport?: { width: number; height: number }
  timezone?: string
  route_context?: Record<string, string>
}

export interface FeedbackRequest {
  category: FeedbackCategory
  message: string
  diagnostics: FeedbackDiagnostics
}

export interface FeedbackResponse {
  id: string
  created: boolean
  notification_status: 'pending' | 'not_configured' | 'sent' | 'failed'
}
```

Add inside `api`:

```ts
  submitFeedback: (data: FeedbackRequest) =>
    apiFetch<FeedbackResponse>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
```

- [ ] **Step 4: Implement FeedbackModal**

Create `frontend/src/components/FeedbackModal.tsx`:

```tsx
import { FormEvent, useId, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, FeedbackCategory, FeedbackDiagnostics } from '../api/client'
import { Button } from './ui/Button'
import { TextArea } from './ui/TextArea'
import { useToast } from './ui/Toast'
import { cn } from '../lib/cn'

interface FeedbackModalProps {
  open: boolean
  onClose: () => void
}

const categories: { label: string; value: FeedbackCategory }[] = [
  { label: 'Feature request', value: 'feature_request' },
  { label: 'Bug', value: 'bug' },
  { label: 'Other', value: 'other' },
]

function routeContextFromPath(pathname: string): Record<string, string> {
  const match = pathname.match(/^\/matches\/([^/?#]+)/)
  return match ? { application_id: decodeURIComponent(match[1]) } : {}
}

export function collectFeedbackDiagnostics(): FeedbackDiagnostics {
  const path = `${window.location.pathname}${window.location.search}`
  return {
    reported_at_client: new Date().toISOString(),
    path,
    page_title: document.title,
    user_agent: window.navigator.userAgent,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    route_context: routeContextFromPath(window.location.pathname),
  }
}

export function FeedbackModal({ open, onClose }: FeedbackModalProps) {
  const toast = useToast()
  const [category, setCategory] = useState<FeedbackCategory>('feature_request')
  const [message, setMessage] = useState('')
  const canSubmit = message.trim().length > 0

  const mutation = useMutation({
    mutationFn: () =>
      api.submitFeedback({
        category,
        message,
        diagnostics: collectFeedbackDiagnostics(),
      }),
    onSuccess: () => {
      setCategory('feature_request')
      setMessage('')
      onClose()
      toast.show('Feedback sent', 'success')
    },
    onError: () => {
      toast.show('Could not send feedback. Try again.', 'error')
    },
  })

  const titleId = useId()

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit || mutation.isPending) return
    mutation.mutate()
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center md:justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <form
        onSubmit={submit}
        className="relative bg-surface-2 border border-border w-full max-w-md rounded-t-lg-token md:rounded-lg-token p-4 outline-none"
      >
        <div className="flex flex-col gap-4">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-text">
              Send feedback
            </h2>
            <p className="mt-1 text-sm text-muted">Page details will be included automatically.</p>
          </div>

          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Feedback category">
            {categories.map((item) => (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-label={item.label}
                aria-checked={category === item.value}
                onClick={() => setCategory(item.value)}
                className={cn(
                  'px-3 py-1.5 rounded-md-token border text-sm',
                  category === item.value
                    ? 'border-accent bg-accent text-accent-fg'
                    : 'border-border text-muted hover:text-text hover:bg-surface',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <TextArea
            label="What happened?"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            maxLength={5000}
          />

          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" pending={mutation.isPending} disabled={!canSubmit}>
              Send
            </Button>
          </div>
        </div>
      </form>
    </div>
  )
}
```

- [ ] **Step 5: Run modal tests**

Run:

```bash
cd frontend && npm run test -- src/components/FeedbackModal.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit frontend modal**

```bash
git add frontend/src/api/client.ts frontend/src/components/FeedbackModal.tsx frontend/src/components/FeedbackModal.test.tsx
git commit -m "feat: add feedback modal"
```

---

### Task 5: App shell entry points

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/AppShell.test.tsx`
- Create or modify: `frontend/src/components/ui/icons/Feedback.tsx`
- Modify: `frontend/src/components/ui/icons/index.ts`

- [ ] **Step 1: Add AppShell tests for feedback entry points**

Modify `frontend/src/components/AppShell.test.tsx`.

Add the feedback endpoint handler in the relevant tests:

```tsx
server.use(
  http.post('/api/feedback', () =>
    HttpResponse.json({ id: 'f-1', created: true, notification_status: 'not_configured' }),
  ),
)
```

Add desktop test:

```tsx
it('opens the feedback modal from the desktop header', async () => {
  const user = userEvent.setup()
  renderShell('/matches/app-123')

  await user.click(screen.getByRole('button', { name: /send feedback/i }))

  expect(screen.getByRole('dialog', { name: /send feedback/i })).toBeInTheDocument()
})
```

Add mobile test inside `describe('AppShell sync (mobile menu)', ...)`:

```tsx
it('opens feedback from the mobile menu', async () => {
  const user = userEvent.setup()
  renderShell()

  await user.click(screen.getByRole('button', { name: /open menu/i }))
  await user.click(screen.getByRole('button', { name: /send feedback/i }))

  expect(screen.getByRole('dialog', { name: /send feedback/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run AppShell tests to verify they fail**

Run:

```bash
cd frontend && npm run test -- src/components/AppShell.test.tsx
```

Expected: FAIL because AppShell has no feedback entry point.

- [ ] **Step 3: Add a feedback icon**

Create `frontend/src/components/ui/icons/Feedback.tsx`:

```tsx
import { SVGProps } from 'react'

export function Feedback(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 9h8M8 13h5" strokeLinecap="round" />
    </svg>
  )
}
```

Modify `frontend/src/components/ui/icons/index.ts`:

```ts
export { Feedback } from './Feedback'
```

- [ ] **Step 4: Integrate FeedbackModal into AppShell**

Modify `frontend/src/components/AppShell.tsx`:

```tsx
import { FeedbackModal } from './FeedbackModal'
import { Settings, Chat, Hamburger, Sync, Feedback as FeedbackIcon } from './ui/icons'
```

Add state:

```tsx
const [feedbackOpen, setFeedbackOpen] = useState(false)
```

Add desktop button near Chat/Settings:

```tsx
<IconButton aria-label="Send feedback" onClick={() => setFeedbackOpen(true)}>
  <FeedbackIcon className="w-5 h-5" />
</IconButton>
```

Add mobile menu item before Chat or Settings:

```tsx
<ActionSheetItem
  onClick={() => { setMenuOpen(false); setFeedbackOpen(true) }}
>
  Send feedback
</ActionSheetItem>
```

Render the modal near the existing `ActionSheet`:

```tsx
<FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
```

- [ ] **Step 5: Run AppShell tests**

Run:

```bash
cd frontend && npm run test -- src/components/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Run modal and shell tests together**

Run:

```bash
cd frontend && npm run test -- src/components/FeedbackModal.test.tsx src/components/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit entry points**

```bash
git add frontend/src/components/AppShell.tsx frontend/src/components/AppShell.test.tsx frontend/src/components/ui/icons/Feedback.tsx frontend/src/components/ui/icons/index.ts
git commit -m "feat: add feedback entry points"
```

---

### Task 6: Full verification and cleanup

**Files:**
- Review all changed files.
- No new files expected unless verification exposes a gap.

- [ ] **Step 1: Run backend feedback tests**

Run:

```bash
pytest tests/integration/test_feedback_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run targeted frontend tests**

Run:

```bash
cd frontend && npm run test -- src/components/FeedbackModal.test.tsx src/components/AppShell.test.tsx src/api/client.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run frontend typecheck**

Run:

```bash
cd frontend && npm run typecheck
```

Expected: PASS.

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 5: Run a focused backend regression slice**

Run:

```bash
pytest tests/integration/test_events_api.py tests/unit/test_config.py tests/unit/test_auth_deps.py -q
```

Expected: PASS.

- [ ] **Step 6: Review git diff**

Run:

```bash
git diff --stat HEAD
git diff HEAD -- app frontend tests alembic
```

Expected:
- No unrelated files changed.
- Webhook payload has no full `message`.
- Diagnostics sanitizer drops unknown keys and size-bounds data.
- Frontend success path ignores `notification_status`.

- [ ] **Step 7: Final commit if verification fixes were needed**

If Step 1-6 required fixes after the last feature commit, stage the exact files changed by those fixes. For example, if only the modal and its test changed:

```bash
git add frontend/src/components/FeedbackModal.tsx frontend/src/components/FeedbackModal.test.tsx
git commit -m "fix: polish feedback reporting"
```

If no fixes were needed, do not create an empty commit.

---

## Spec Coverage Checklist

- Authenticated feedback from current page: Tasks 2, 4, 5.
- Durable Postgres storage: Tasks 1, 2.
- Category selector with `Feature request`, `Bug`, `Other`: Tasks 4, 5.
- Conservative diagnostics only: Tasks 2, 4.
- Diagnostics allowlisting and 16 KB cap: Task 2.
- Generic webhook notification: Tasks 2, 3.
- No full message in webhook payload: Task 3.
- Notification failure invisible to users after DB write: Tasks 2, 3, 4.
- Header and mobile menu entry points: Task 5.
- Tests and verification: Tasks 1-6.

## Notes For Implementers

- Keep each task commit focused. If a task requires changing earlier snippets, update tests first and keep the final behavior aligned with the approved design spec.
- Do not add anonymous feedback, screenshots, Axiom alerting, Slack-specific payloads, email delivery, or an admin UI in this implementation.
