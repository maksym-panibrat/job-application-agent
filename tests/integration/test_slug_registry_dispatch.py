"""Tests for slug_registry_service.validate_slug dispatch via SOURCES."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import slug_registry_service


@pytest.mark.asyncio
async def test_validate_slug_dispatches_to_lever(db_session):
    fake_lever = AsyncMock()
    fake_lever.validate = AsyncMock(return_value=True)
    with patch.dict(
        "app.services.slug_registry_service.SOURCES",
        {"lever": fake_lever},
        clear=False,
    ):
        ok = await slug_registry_service.validate_slug("lever", "acme", db_session)
    assert ok is True
    fake_lever.validate.assert_awaited_once_with("acme")


@pytest.mark.asyncio
async def test_validate_slug_unknown_provider_raises(db_session):
    with pytest.raises(ValueError, match="unknown provider"):
        await slug_registry_service.validate_slug("myspace", "acme", db_session)


@pytest.mark.asyncio
async def test_validate_slug_returns_false_on_404(db_session):
    fake = AsyncMock()
    fake.validate = AsyncMock(return_value=False)
    with patch.dict(
        "app.services.slug_registry_service.SOURCES",
        {"ashby": fake},
        clear=False,
    ):
        ok = await slug_registry_service.validate_slug("ashby", "missing", db_session)
    assert ok is False
