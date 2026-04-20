from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import ResourceExhausted

from app.agents.llm_safe import BudgetExhausted
from app.services.resume_extraction import (
    InvalidResumeError,
    LLMUnavailableError,
    extract_profile_from_resume,
)


def _settings():
    s = MagicMock()
    s.environment = "test"
    return s


@pytest.mark.asyncio
async def test_resource_exhausted_raises_llm_unavailable():
    with (
        patch("app.services.resume_extraction.get_settings", return_value=_settings()),
        patch(
            "app.services.resume_extraction.safe_ainvoke",
            side_effect=ResourceExhausted("quota"),
        ),
    ):
        with pytest.raises(LLMUnavailableError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_budget_exhausted_raises_llm_unavailable():
    with (
        patch("app.services.resume_extraction.get_settings", return_value=_settings()),
        patch(
            "app.services.resume_extraction.safe_ainvoke",
            side_effect=BudgetExhausted(datetime(2099, 1, 1, tzinfo=UTC)),
        ),
    ):
        with pytest.raises(LLMUnavailableError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_invalid_json_raises_invalid_resume():
    mock_resp = MagicMock()
    mock_resp.content = "not json {{"
    with (
        patch("app.services.resume_extraction.get_settings", return_value=_settings()),
        patch("app.services.resume_extraction.safe_ainvoke", return_value=mock_resp),
    ):
        with pytest.raises(InvalidResumeError):
            await extract_profile_from_resume("resume text")


@pytest.mark.asyncio
async def test_valid_json_returns_dict():
    mock_resp = MagicMock()
    mock_resp.content = '{"full_name": "Jane Doe", "skills": []}'
    with (
        patch("app.services.resume_extraction.get_settings", return_value=_settings()),
        patch("app.services.resume_extraction.safe_ainvoke", return_value=mock_resp),
    ):
        result = await extract_profile_from_resume("resume text")
        assert result["full_name"] == "Jane Doe"
