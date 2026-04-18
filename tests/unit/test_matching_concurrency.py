# Regression: matching agent fan-out sent all N jobs to the Anthropic API
# simultaneously with no concurrency cap, causing 429 "concurrent connections
# exceeded" errors during job sync.
# Discovered via runtime error on POST /api/jobs/sync.

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.agents.matching_agent import build_graph


def _make_job(i: int) -> dict:
    return {
        "application_id": f"app-{i}",
        "title": f"Job {i}",
        "company": f"Company {i}",
        "description": "A job description.",
    }


def _fake_llm_response():
    """Return a mock that looks like a ChatAnthropic response with tool_calls."""
    resp = MagicMock()
    resp.tool_calls = [
        {
            "name": "record_score",
            "args": {
                "score": 0.5,
                "rationale": "decent match",
                "strengths": ["a"],
                "gaps": ["b"],
            },
        }
    ]
    return resp


@pytest.mark.asyncio
async def test_matching_agent_limits_concurrent_api_calls():
    """Fan-out scoring must not exceed matching_max_concurrency simultaneous
    API calls. Before the fix, all N jobs called the LLM at once."""
    max_concurrency = 3
    num_jobs = 10
    peak_concurrent = {"value": 0}
    active = {"value": 0}
    lock = threading.Lock()

    def tracking_invoke(messages, **kwargs):
        with lock:
            active["value"] += 1
            peak_concurrent["value"] = max(peak_concurrent["value"], active["value"])
        time.sleep(0.05)  # simulate API latency
        with lock:
            active["value"] -= 1
        return _fake_llm_response()

    mock_llm = MagicMock()
    mock_llm.invoke = tracking_invoke
    mock_bound = MagicMock()
    mock_bound.invoke = tracking_invoke
    mock_llm.bind_tools.return_value = mock_bound

    with (
        patch(
            "app.agents.matching_agent.get_llm",
            return_value=mock_llm,
        ),
        patch(
            "app.agents.matching_agent.get_settings",
        ) as mock_settings,
    ):
        settings = MagicMock()
        settings.matching_max_concurrency = max_concurrency
        mock_settings.return_value = settings

        graph = build_graph()
        result = await graph.ainvoke(
            {
                "profile_id": "test-profile",
                "profile_text": "I am a software engineer.",
                "jobs": [_make_job(i) for i in range(num_jobs)],
                "scores": [],
            }
        )

    assert len(result["scores"]) == num_jobs
    assert peak_concurrent["value"] <= max_concurrency, (
        f"Peak concurrent API calls ({peak_concurrent['value']}) exceeded "
        f"max_concurrency ({max_concurrency})"
    )
