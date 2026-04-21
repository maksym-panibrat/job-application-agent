"""Unit tests for greenhouse.get_job_questions and get_job_questions_by_url."""

import httpx
import pytest
import respx

from app.sources.greenhouse import get_job_questions, get_job_questions_by_url

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"

QUESTIONS_RESPONSE = {
    "questions": [
        {"label": "Cover Letter", "type": "textarea", "required": False},
        {"label": "Years of Experience", "type": "input_text", "required": True},
        {"label": "Work Authorization", "type": "multi_value_single_select", "required": True},
        {"label": "Resume", "type": "input_file", "required": True},
        {"label": "Portfolio", "type": "attachment", "required": False},
    ]
}


@pytest.mark.asyncio
async def test_get_job_questions_happy_path():
    with respx.mock:
        respx.get(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json=QUESTIONS_RESPONSE)
        )
        result = await get_job_questions("exampleco", "12345")

    assert len(result) == 3
    labels = {q["label"] for q in result}
    assert labels == {"Cover Letter", "Years of Experience", "Work Authorization"}


@pytest.mark.asyncio
async def test_get_job_questions_filters_input_file():
    with respx.mock:
        respx.get(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json=QUESTIONS_RESPONSE)
        )
        result = await get_job_questions("exampleco", "12345")

    types = {q["type"] for q in result}
    assert "input_file" not in types
    assert "attachment" not in types


@pytest.mark.asyncio
async def test_get_job_questions_returns_question_structure():
    with respx.mock:
        respx.get(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json=QUESTIONS_RESPONSE)
        )
        result = await get_job_questions("exampleco", "12345")

    for q in result:
        assert "label" in q
        assert "type" in q
        assert "required" in q


@pytest.mark.asyncio
async def test_get_job_questions_returns_empty_on_404():
    with respx.mock:
        respx.get(f"{BOARDS_API}/badco/jobs/99999").mock(
            return_value=httpx.Response(404)
        )
        result = await get_job_questions("badco", "99999")

    assert result == []


@pytest.mark.asyncio
async def test_get_job_questions_returns_empty_on_network_error():
    with respx.mock:
        respx.get(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await get_job_questions("exampleco", "12345")

    assert result == []


@pytest.mark.asyncio
async def test_get_job_questions_by_url_valid_greenhouse_url():
    apply_url = "https://boards.greenhouse.io/exampleco/jobs/12345"
    with respx.mock:
        respx.get(f"{BOARDS_API}/exampleco/jobs/12345").mock(
            return_value=httpx.Response(200, json={"questions": [
                {"label": "Why us?", "type": "textarea", "required": True},
            ]})
        )
        result = await get_job_questions_by_url(apply_url)

    assert len(result) == 1
    assert result[0]["label"] == "Why us?"


@pytest.mark.asyncio
async def test_get_job_questions_by_url_non_greenhouse_url():
    result = await get_job_questions_by_url("https://jobs.lever.co/acme/abc-123")
    assert result == []


@pytest.mark.asyncio
async def test_get_job_questions_by_url_greenhouse_url_missing_job_id():
    result = await get_job_questions_by_url("https://boards.greenhouse.io/exampleco")
    assert result == []
