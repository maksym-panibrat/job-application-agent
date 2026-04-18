"""Chat endpoint — streams onboarding agent responses via SSE."""

import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db, get_session_factory
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/messages")
async def send_message(
    request: Request,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """
    Send a message to the onboarding agent and stream the response.
    POST body: {"message": "..."}
    Response: text/event-stream
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return {"error": "message is required"}

    # Get checkpointer from app state (set up in lifespan)
    app_state = request.app.state
    checkpointer = getattr(app_state, "checkpointer", None)

    if checkpointer is None:
        async def no_op():
            msg = json.dumps({"content": "Agent not available — checkpointer not initialized."})
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(no_op(), media_type="text/event-stream")

    from app.agents.onboarding import build_graph

    graph = build_graph(checkpointer)
    thread_id = str(profile.id)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_factory": get_session_factory(),
            "profile_id": str(profile.id),
        }
    }

    # Build graph input — always pass profile_id and current resume_md
    graph_input: dict = {
        "messages": [{"role": "user", "content": user_message}],
        "profile_id": str(profile.id),
        "resume_md": profile.base_resume_md,
        "profile_updates": {},
    }

    async def stream_response():
        try:
            from langchain_core.messages import AIMessageChunk

            async for chunk in graph.astream(
                graph_input,
                config,
                stream_mode="messages",
            ):
                if not (isinstance(chunk, tuple) and len(chunk) == 2):
                    continue
                msg, metadata = chunk
                # Only forward AI response text; skip ToolMessages and non-agent nodes
                if not isinstance(msg, AIMessageChunk):
                    continue
                content = msg.content
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text += block.get("text", "")
                if text:
                    yield f"data: {json.dumps({'content': text})}\n\n"
        except Exception as e:
            await log.aexception("chat.stream_error", error=str(e))
            yield f"data: {json.dumps({'error': 'Stream error'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
