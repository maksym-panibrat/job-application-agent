"""Unit tests for greenhouse.try_submit."""

import httpx
import pytest
import respx

from app.sources.greenhouse import try_submit

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"
APPLY_URL = "https://boards.greenhouse.io/exampleco/jobs/12345"


@pytest.mark.asyncio
async def test_try_submit_happy_200():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        result = await try_submit(
            apply_url=APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["method"] == "greenhouse_api"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_try_submit_happy_201():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(201, json={"id": 2})
        )
        result = await try_submit(
            apply_url=APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is True
    assert result["method"] == "greenhouse_api"


@pytest.mark.asyncio
async def test_try_submit_401_returns_failure():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        result = await try_submit(
            apply_url=APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_try_submit_network_error_returns_failure():
    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await try_submit(
            apply_url=APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_try_submit_post_body_contains_name_and_email():
    captured = {}

    async def capture_request(request, route):
        captured["body"] = request.content
        return httpx.Response(200, json={})

    with respx.mock:
        respx.post(f"{BOARDS_API}/exampleco/jobs/12345").mock(side_effect=capture_request)
        await try_submit(
            apply_url=APPLY_URL,
            first_name="Jane",
            last_name="Doe",
            email="jane@example.com",
        )

    import json
    body = json.loads(captured["body"])
    assert body["first_name"] == "Jane"
    assert body["last_name"] == "Doe"
    assert body["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_try_submit_no_board_token_returns_manual():
    result = await try_submit(
        apply_url="https://jobs.lever.co/acme/abc",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
    )
    assert result["success"] is False
    assert result["method"] == "manual"


@pytest.mark.asyncio
async def test_try_submit_no_job_id_returns_manual():
    result = await try_submit(
        apply_url="https://boards.greenhouse.io/exampleco",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
    )
    assert result["success"] is False
    assert result["method"] == "manual"
