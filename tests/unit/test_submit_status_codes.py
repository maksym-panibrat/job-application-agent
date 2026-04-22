"""
Unit tests for ATS status-code propagation through greenhouse.try_submit and
lever_submit.try_submit, and the HTTP status mapping logic extracted from the
submit endpoint.

These tests use respx to mock HTTP and do not require a database.
"""

import httpx
import pytest
import respx

from app.sources.greenhouse import try_submit as greenhouse_try_submit
from app.sources.lever_submit import try_submit as lever_try_submit

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"
GH_APPLY_URL = "https://boards.greenhouse.io/exampleco/jobs/12345"
LEVER_APPLY_URL = "https://jobs.lever.co/acmecorp/abc-1234-def"
LEVER_API = "https://api.lever.co/v0/postings/acmecorp/abc-1234-def"


# ---------------------------------------------------------------------------
# Greenhouse: status_code propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_greenhouse_success_carries_status_code_200():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["method"] == "greenhouse_api"


@pytest.mark.asyncio
async def test_greenhouse_success_carries_status_code_201():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(201, json={"id": 2})
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is True
    assert result["status_code"] == 201


@pytest.mark.asyncio
async def test_greenhouse_422_carries_status_code():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(422, text="Unprocessable Entity")
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert result["status_code"] == 422
    assert result["method"] == "greenhouse_api"
    assert "error" in result


@pytest.mark.asyncio
async def test_greenhouse_503_carries_status_code():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert result["status_code"] == 503
    assert result["method"] == "greenhouse_api"


@pytest.mark.asyncio
async def test_greenhouse_timeout_sets_status_code_none():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert result["status_code"] is None
    assert result["method"] == "greenhouse_api"
    assert "error" in result


@pytest.mark.asyncio
async def test_greenhouse_network_error_sets_status_code_none():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await greenhouse_try_submit(
            apply_url=GH_APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert result["status_code"] is None
    assert result["method"] == "greenhouse_api"


# ---------------------------------------------------------------------------
# Lever: status_code propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lever_500_carries_status_code():
    with respx.mock:
        respx.post(LEVER_API).mock(return_value=httpx.Response(500, text="Internal Server Error"))
        result = await lever_try_submit(
            apply_url=LEVER_APPLY_URL,
            resume_text="My resume",
            cover_letter_md="My cover letter",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            api_key="test-api-key",
        )

    assert result["success"] is False
    assert result["status_code"] == 500
    assert result["method"] == "lever_api"


@pytest.mark.asyncio
async def test_lever_network_error_sets_status_code_none():
    with respx.mock:
        respx.post(LEVER_API).mock(side_effect=httpx.ConnectError("connection refused"))
        result = await lever_try_submit(
            apply_url=LEVER_APPLY_URL,
            resume_text="My resume",
            cover_letter_md="My cover letter",
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
            api_key="test-api-key",
        )

    assert result["success"] is False
    assert result["status_code"] is None
    assert result["method"] == "lever_api"
    assert "error" in result


# ---------------------------------------------------------------------------
# HTTP status mapping logic (tested via the endpoint in integration tests;
# here we test the mapping rules directly via a thin helper to keep it fast)
# ---------------------------------------------------------------------------


def _map_result_to_http(result: dict) -> int:
    """Mirrors the mapping logic in app/api/applications.py::submit_application."""
    result_method = result.get("method", "")
    if result_method in ("manual", "needs_review", "dry_run"):
        return 200
    if result.get("success"):
        return 200
    status_code = result.get("status_code")
    if status_code is None:
        return 502
    if 400 <= status_code < 500:
        return 400
    if 500 <= status_code < 600:
        return 502
    return 200


def test_mapping_success_returns_200():
    result = {"method": "greenhouse_api", "success": True, "status_code": 200}
    assert _map_result_to_http(result) == 200


def test_mapping_4xx_returns_400():
    result = {"method": "greenhouse_api", "success": False, "status_code": 422}
    assert _map_result_to_http(result) == 400


def test_mapping_4xx_boundary_400_returns_400():
    result = {"method": "greenhouse_api", "success": False, "status_code": 400}
    assert _map_result_to_http(result) == 400


def test_mapping_4xx_boundary_499_returns_400():
    result = {"method": "greenhouse_api", "success": False, "status_code": 499}
    assert _map_result_to_http(result) == 400


def test_mapping_5xx_returns_502():
    result = {"method": "greenhouse_api", "success": False, "status_code": 503}
    assert _map_result_to_http(result) == 502


def test_mapping_5xx_boundary_500_returns_502():
    result = {"method": "lever_api", "success": False, "status_code": 500}
    assert _map_result_to_http(result) == 502


def test_mapping_none_status_code_returns_502():
    result = {"method": "greenhouse_api", "success": False, "status_code": None}
    assert _map_result_to_http(result) == 502


def test_mapping_manual_always_200():
    assert _map_result_to_http({"method": "manual", "apply_url": "https://example.com"}) == 200


def test_mapping_dry_run_always_200():
    assert _map_result_to_http({"method": "dry_run", "would_submit": True}) == 200


def test_mapping_needs_review_always_200():
    assert _map_result_to_http({"method": "needs_review", "unanswered_questions": ["Q1"]}) == 200
