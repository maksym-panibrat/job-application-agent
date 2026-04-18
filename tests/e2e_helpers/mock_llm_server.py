"""
Minimal Anthropic Messages API mock for Playwright E2E testing.

Handles POST /v1/messages (both streaming and non-streaming) and returns
deterministic responses. Set ANTHROPIC_BASE_URL=http://localhost:9000 in the
backend env to redirect LLM calls here.

Run: uv run python tests/e2e_helpers/mock_llm_server.py
"""

import json
import os
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


def _msg_id() -> str:
    return f"msg_mock_{uuid.uuid4().hex[:8]}"


def _tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Determine which response to give based on request content
# ---------------------------------------------------------------------------


def _route_request(body: dict) -> tuple[list, str]:
    """
    Return (content_blocks, stop_reason) for the given request body.
    content_blocks follow the Anthropic content block schema.
    """
    system = body.get("system", "")
    if isinstance(system, list):
        system = " ".join(
            s.get("text", "") if isinstance(s, dict) else str(s) for s in system
        )
    system = system.lower()
    messages = body.get("messages", [])
    tools = body.get("tools", [])

    if "job application assistant" in system or "target roles" in system:
        return _onboarding_blocks(messages, tools)
    if "screener" in system or "rate how well" in system:
        return _scoring_blocks()
    if "resume writer" in system or "cover letter writer" in system:
        return _generation_blocks(system)

    # Resume extraction: single user message, no tools, prompt mentions resume
    if messages and not tools:
        first = messages[0]
        content = first.get("content", "")
        if isinstance(content, str) and "resume" in content.lower():
            return _resume_extraction_blocks()

    return [{"type": "text", "text": "I can help you with that."}], "end_turn"


def _onboarding_blocks(messages: list, tools: list) -> tuple[list, str]:
    has_tool_result = any(
        isinstance(m.get("content"), list)
        and any(c.get("type") == "tool_result" for c in m.get("content", []))
        for m in messages
    )
    save_tool = any(t.get("name") == "save_profile_updates" for t in tools)

    if save_tool and not has_tool_result:
        updates = json.dumps(
            {
                "full_name": "Jane Smith",
                "email": "jane@example.com",
                "target_roles": ["Senior Software Engineer", "Staff Engineer"],
                "seniority": "senior",
                "target_locations": ["San Francisco"],
                "remote_ok": True,
                "search_keywords": ["Python", "distributed systems"],
                "skills": [
                    {
                        "name": "Python",
                        "category": "language",
                        "proficiency": "expert",
                        "years": 7,
                    },
                    {
                        "name": "FastAPI",
                        "category": "framework",
                        "proficiency": "expert",
                        "years": 3,
                    },
                ],
            }
        )
        block = {
            "type": "tool_use",
            "id": _tool_id(),
            "name": "save_profile_updates",
            "input": {"updates": updates},
        }
        return [block], "tool_use"

    text = (
        "I've saved your profile. You're targeting Senior Software Engineer roles "
        "in San Francisco. Is there anything else you'd like to update?"
    )
    return [{"type": "text", "text": text}], "end_turn"


def _scoring_blocks() -> tuple[list, str]:
    data = {
        "score": 0.85,
        "rationale": "Strong technical match.",
        "strengths": ["Python expertise", "FastAPI"],
        "gaps": [],
    }
    return [{"type": "text", "text": json.dumps(data)}], "end_turn"


def _generation_blocks(system: str) -> tuple[list, str]:
    if "cover letter" in system:
        text = (
            "Dear Hiring Manager,\n\nI am excited to apply for this position."
            "\n\nBest regards,\nJane Smith"
        )
    else:
        text = (
            "# Jane Smith\njane@example.com\n\n## Experience\n"
            "Senior Software Engineer at Acme Corp (2020-present)"
        )
    return [{"type": "text", "text": text}], "end_turn"


def _resume_extraction_blocks() -> tuple[list, str]:
    data = {
        "full_name": "Jane Smith",
        "email": "jane@example.com",
        "phone": "+1-555-0100",
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "github_url": "https://github.com/janesmith",
        "portfolio_url": None,
        "target_roles": ["Senior Software Engineer", "Staff Engineer"],
        "skills": [
            {"name": "Python", "category": "language", "proficiency": "expert", "years": 7},
            {"name": "FastAPI", "category": "framework", "proficiency": "expert", "years": 3},
            {
                "name": "PostgreSQL",
                "category": "tool",
                "proficiency": "proficient",
                "years": 5,
            },
        ],
        "work_experiences": [
            {
                "company": "Acme Corp",
                "title": "Senior Software Engineer",
                "start_date": "2020-01-01",
                "end_date": None,
                "description_md": "Led backend platform development.",
                "technologies": ["Python", "FastAPI", "PostgreSQL"],
            }
        ],
    }
    return [{"type": "text", "text": json.dumps(data)}], "end_turn"


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _build_json_response(content_blocks: list, stop_reason: str, model: str) -> dict:
    return {
        "id": _msg_id(),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


async def _stream_response(content_blocks: list, stop_reason: str, model: str):
    """Yield SSE events matching the Anthropic streaming Messages API format."""
    msg_id = _msg_id()
    usage_in = 100

    # message_start
    yield (
        "event: message_start\n"
        "data: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": usage_in, "output_tokens": 0},
                },
            }
        )
        + "\n\n"
    )

    for i, block in enumerate(content_blocks):
        if block["type"] == "text":
            # content_block_start (empty text)
            yield (
                "event: content_block_start\n"
                "data: "
                + json.dumps(
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {"type": "text", "text": ""},
                    }
                )
                + "\n\n"
            )
            # stream text in chunks
            text = block["text"]
            chunk_size = 30
            for j in range(0, len(text), chunk_size):
                yield (
                    "event: content_block_delta\n"
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_delta",
                            "index": i,
                            "delta": {"type": "text_delta", "text": text[j : j + chunk_size]},
                        }
                    )
                    + "\n\n"
                )
        elif block["type"] == "tool_use":
            # content_block_start (tool_use with empty input)
            yield (
                "event: content_block_start\n"
                "data: "
                + json.dumps(
                    {
                        "type": "content_block_start",
                        "index": i,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    }
                )
                + "\n\n"
            )
            # stream the input JSON
            input_json = json.dumps(block["input"])
            yield (
                "event: content_block_delta\n"
                "data: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": input_json},
                    }
                )
                + "\n\n"
            )

        # content_block_stop
        yield (
            "event: content_block_stop\n"
            "data: "
            + json.dumps({"type": "content_block_stop", "index": i})
            + "\n\n"
        )

    # message_delta
    yield (
        "event: message_delta\n"
        "data: "
        + json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 50},
            }
        )
        + "\n\n"
    )

    # message_stop
    yield "event: message_stop\n" "data: " + json.dumps({"type": "message_stop"}) + "\n\n"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    model = body.get("model", "claude-haiku-4-5-20251001")
    is_streaming = body.get("stream", False)

    content_blocks, stop_reason = _route_request(body)

    if is_streaming:
        return StreamingResponse(
            _stream_response(content_blocks, stop_reason, model),
            media_type="text/event-stream",
        )

    return JSONResponse(_build_json_response(content_blocks, stop_reason, model))


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_LLM_PORT", 9000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
