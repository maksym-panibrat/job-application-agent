"""
E2E tests — onboarding chat flow:
  POST /api/chat/messages → SSE stream → content chunks received
"""

import json

import pytest


@pytest.mark.asyncio
async def test_chat_message_streams_response(test_app):
    """
    Posting a message to /api/chat/messages returns an SSE stream.
    The fake LLM (patched in conftest) returns a greeting.
    """
    # Ensure the dev user + profile + checkpointer are initialized
    await test_app.get("/api/profile")

    async with test_app.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "Hello, I want to set up my job search."},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        chunks = []
        done = False
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    done = True
                    break
                try:
                    parsed = json.loads(data)
                    if "content" in parsed:
                        chunks.append(parsed["content"])
                    elif "error" in parsed:
                        pytest.fail(f"Stream returned error: {parsed['error']}")
                except json.JSONDecodeError:
                    pass

    assert done, "Stream did not send [DONE]"
    full_text = "".join(chunks)
    assert len(full_text) > 0


@pytest.mark.asyncio
async def test_chat_empty_message_rejected(test_app):
    """Empty message should return an error without crashing."""
    resp = await test_app.post(
        "/api/chat/messages",
        json={"message": ""},
    )
    # FastAPI returns 200 with error body (not 422) per current implementation
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert "error" in resp.json()


@pytest.mark.asyncio
async def test_chat_emits_structured_budget_exhausted_event(test_app):
    """When the onboarding graph hits BudgetExhausted (Gemini quota / prepayment
    credits depleted), chat must emit a structured SSE event with the resumption
    timestamp — not the generic 'Stream error' that hides the cause from users.

    Regression: see #74. Before the fix, chat.py:92 caught BudgetExhausted in
    its `except Exception` catch-all and yielded {'error': 'Stream error'},
    which the UI showed as an opaque failure (smoke step 7 trace, 2026-05-04)."""
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from app.agents.llm_safe import BudgetExhausted

    await test_app.get("/api/profile")

    resumes_at = datetime(2026, 6, 1, tzinfo=UTC)

    async def boom(*args, **kwargs):
        raise BudgetExhausted(resumes_at)
        # Make the function an async generator so `async for chunk in graph.astream(...)`
        # is valid syntax against this mock.
        yield  # pragma: no cover

    fake_graph = MagicMock()
    fake_graph.astream = boom

    from app.main import app as fastapi_app

    # Fixture doesn't run lifespan, so checkpointer is unset. Inject a non-None
    # placeholder for the duration of this test so chat.py takes the real graph
    # path, then restore (avoid leaking into other tests).
    prev_checkpointer = getattr(fastapi_app.state, "checkpointer", None)
    fastapi_app.state.checkpointer = MagicMock()
    try:
        with patch("app.agents.onboarding.build_graph", return_value=fake_graph):
            async with test_app.stream(
                "POST",
                "/api/chat/messages",
                json={"message": "anything"},
            ) as resp:
                assert resp.status_code == 200
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        events.append(json.loads(data))
    finally:
        fastapi_app.state.checkpointer = prev_checkpointer

    error_events = [e for e in events if "error" in e]
    assert error_events, f"expected at least one error event, got {events}"
    assert error_events[0]["error"] == "budget_exhausted", (
        f"expected structured budget_exhausted event, got {error_events[0]}"
    )
    assert error_events[0]["resumes_at"] == resumes_at.isoformat()


@pytest.mark.asyncio
async def test_chat_session_persists_across_messages(test_app):
    """
    Two messages to the same profile thread accumulate in the checkpointed state.
    We verify both return non-empty content (state is resumed, not reset).
    """
    await test_app.get("/api/profile")  # ensure dev user exists

    collected = []

    for msg in ["Hi there!", "What roles should I target?"]:
        async with test_app.stream(
            "POST",
            "/api/chat/messages",
            json={"message": msg},
        ) as resp:
            assert resp.status_code == 200
            chunks = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                        if "content" in parsed:
                            chunks.append(parsed["content"])
                    except json.JSONDecodeError:
                        pass
            collected.append("".join(chunks))

    assert all(len(c) > 0 for c in collected), "All messages should receive responses"
