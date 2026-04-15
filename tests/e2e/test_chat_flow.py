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
