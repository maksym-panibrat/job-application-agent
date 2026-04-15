"""Chat endpoint — streams onboarding agent responses via SSE."""

import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


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
        # Fallback: no-op response if checkpointer not initialized
        async def no_op():
            msg = json.dumps({"content": "Agent not available — checkpointer not initialized."})
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(no_op(), media_type="text/event-stream")

    from app.agents.onboarding import build_graph

    graph = build_graph(checkpointer)
    thread_id = str(profile.id)
    config = {"configurable": {"thread_id": thread_id}}

    async def stream_response():
        try:
            async for chunk in graph.astream(
                {"messages": [{"role": "user", "content": user_message}]},
                config,
                stream_mode="messages",
            ):
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    msg, metadata = chunk
                    content = getattr(msg, "content", "")
                    if content and isinstance(content, str):
                        yield f"data: {json.dumps({'content': content})}\n\n"
        except Exception as e:
            await log.aexception("chat.stream_error", error=str(e))
            yield f"data: {json.dumps({'error': 'Stream error'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
