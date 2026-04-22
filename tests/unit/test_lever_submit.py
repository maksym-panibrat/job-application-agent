"""Unit tests for lever_submit.try_submit."""

import httpx
import pytest
import respx

from app.sources.lever_submit import try_submit

LEVER_URL = "https://jobs.lever.co/acmecorp/abc-1234-def"
LEVER_API = "https://api.lever.co/v0/postings/acmecorp/abc-1234-def"


@pytest.mark.asyncio
async def test_try_submit_no_api_key_returns_manual():
    result = await try_submit(
        apply_url=LEVER_URL,
        resume_text="My resume",
        cover_letter_md="My cover letter",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        api_key=None,
    )
    assert result["method"] == "manual"
    assert result["apply_url"] == LEVER_URL


@pytest.mark.asyncio
async def test_try_submit_non_lever_url_returns_manual():
    result = await try_submit(
        apply_url="https://example.com/apply",
        resume_text="My resume",
        cover_letter_md="My cover letter",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        api_key="some-key",
    )
    assert result["method"] == "manual"
    assert result["apply_url"] == "https://example.com/apply"


@pytest.mark.asyncio
async def test_try_submit_lever_happy_path():
    with respx.mock:
        respx.post(LEVER_API).mock(return_value=httpx.Response(200, text="OK"))
        result = await try_submit(
            apply_url=LEVER_URL,
            resume_text="My resume",
            cover_letter_md="My cover letter",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            api_key="test-api-key",
        )

    assert result["method"] == "lever_api"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_try_submit_lever_422_returns_failure():
    with respx.mock:
        respx.post(LEVER_API).mock(return_value=httpx.Response(422, text="Unprocessable"))
        result = await try_submit(
            apply_url=LEVER_URL,
            resume_text="My resume",
            cover_letter_md="My cover letter",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            api_key="test-api-key",
        )

    assert result["method"] == "lever_api"
    assert result["success"] is False
    assert result["status_code"] == 422


@pytest.mark.asyncio
async def test_try_submit_lever_network_error_returns_unreachable():
    with respx.mock:
        respx.post(LEVER_API).mock(side_effect=httpx.ConnectError("connection refused"))
        result = await try_submit(
            apply_url=LEVER_URL,
            resume_text="My resume",
            cover_letter_md="My cover letter",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            api_key="test-api-key",
        )

    assert result["method"] == "lever_api"
    assert result["success"] is False
    assert result["status_code"] is None
    assert "error" in result
