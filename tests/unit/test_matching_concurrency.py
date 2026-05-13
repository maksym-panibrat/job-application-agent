# Phase B moves concurrency control out of the matching agent and into the
# worker queue. This file keeps the old fan-out surface under test so a future
# hidden throttle does not reappear inside the agent.

import asyncio
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
async def test_matching_agent_has_no_internal_concurrency_throttle():
    """Worker concurrency is the throttle; the graph fan-out stays unbounded."""
    num_jobs = 10
    call_lock = asyncio.Lock()
    active = {"value": 0}
    peak_concurrent = {"value": 0}

    async def tracking_ainvoke(messages, **kwargs):
        async with call_lock:
            active["value"] += 1
            peak_concurrent["value"] = max(peak_concurrent["value"], active["value"])
        await asyncio.sleep(0.05)  # simulate API latency
        async with call_lock:
            active["value"] -= 1
        return _fake_llm_response()

    mock_llm = MagicMock()
    mock_bound = MagicMock()
    mock_bound.ainvoke = tracking_ainvoke
    mock_llm.bind_tools.return_value = mock_bound

    with patch("app.agents.matching_agent.get_llm", return_value=mock_llm):
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
    assert peak_concurrent["value"] == num_jobs
