"""Unit tests for background-task crash recovery in app.api.applications.

Locks in the invariant that if ``_resume_in_background`` or
``_generate_in_background`` crashes before the service layer's own
try/except sets status=failed, the helper still opens a fresh session and
flips ``generating`` -> ``failed`` so the row is never pinned.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _env():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("GOOGLE_API_KEY", "fake")
    yield


def _fake_row_in_state(status: str) -> MagicMock:
    row = MagicMock()
    row.generation_status = status
    return row


class _SessionCtx:
    """async context manager wrapping a mock session."""

    def __init__(self, session: MagicMock):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _session_factory_with(row: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Return (factory_callable, session_mock)."""
    session = MagicMock()
    session.get = AsyncMock(return_value=row)
    session.add = MagicMock()
    session.commit = AsyncMock()

    def _factory_call():
        return _SessionCtx(session)

    return _factory_call, session


@pytest.mark.asyncio
async def test_resume_background_crash_sets_failed(monkeypatch):
    """
    If ``resume_generation`` raises BEFORE its internal try/except fires
    (e.g. module import / session acquisition fails — simulated here by
    patching ``resume_generation`` itself to raise), the helper must open a
    fresh session and flip status -> 'failed'.
    """
    from app import database as db_mod
    from app.api import applications as mod
    from app.services import application_service

    row = _fake_row_in_state("generating")
    factory, session = _session_factory_with(row)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: factory)

    async def _boom(*a, **kw):
        raise RuntimeError("pre-service crash")

    monkeypatch.setattr(application_service, "resume_generation", _boom)

    await mod._resume_in_background(uuid.uuid4(), {"approved": True}, object())

    # After recovery, row should have been flipped to "failed" and committed.
    assert row.generation_status == "failed"
    assert session.commit.await_count >= 1


@pytest.mark.asyncio
async def test_resume_background_crash_skips_if_not_generating(monkeypatch):
    """Recovery must NOT clobber a terminal status already written elsewhere."""
    from app import database as db_mod
    from app.api import applications as mod
    from app.services import application_service

    row = _fake_row_in_state("ready")  # already terminal
    factory, session = _session_factory_with(row)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: factory)

    async def _boom(*a, **kw):
        raise RuntimeError("pre-service crash")

    monkeypatch.setattr(application_service, "resume_generation", _boom)

    await mod._resume_in_background(uuid.uuid4(), {"approved": True}, object())

    assert row.generation_status == "ready"
    # No commit because the guard short-circuited
    assert session.commit.await_count == 0


@pytest.mark.asyncio
async def test_generate_background_crash_sets_failed(monkeypatch):
    """Same invariant for ``_generate_in_background``."""
    from app import database as db_mod
    from app.api import applications as mod
    from app.services import application_service

    row = _fake_row_in_state("generating")
    factory, session = _session_factory_with(row)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: factory)

    async def _boom(*a, **kw):
        raise RuntimeError("pre-service crash")

    monkeypatch.setattr(application_service, "generate_materials", _boom)

    await mod._generate_in_background(uuid.uuid4(), object())

    assert row.generation_status == "failed"
    assert session.commit.await_count >= 1
