"""Chat endpoint — streams onboarding agent responses via SSE.

If the agent mutates the user's profile during a turn (detected via a
before/after snapshot of profile.updated_at), the endpoint emits an
`event: meta` payload before the terminal `[DONE]`. The payload reports both
the mutation and whether the profile has enough provider slugs for a manual
search to actually start.
"""

import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.api.deps import get_current_profile
from app.database import get_db, get_session_factory
from app.models.company import Company
from app.models.user_profile import UserProfile

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])


async def _profile_updated_at(session_factory, profile_id):
    """Read profile.updated_at in a fresh session — the request-scoped
    session may be in the middle of an unrelated transaction."""
    async with session_factory() as s:
        row = (
            await s.execute(select(UserProfile.updated_at).where(UserProfile.id == profile_id))
        ).first()
    return row[0] if row else None


async def _profile_can_start_search(session_factory, profile_id) -> bool:
    """Return true only when /api/jobs/sync has at least one provider slug pair
    to enqueue and the location gate is satisfied."""
    async with session_factory() as s:
        profile = await s.get(UserProfile, profile_id)
        if profile is None:
            return False

        has_location = bool(profile.target_locations) or bool(profile.remote_ok)
        company_ids = list(profile.target_company_ids or [])
        if not has_location or not company_ids:
            return False

        rows = (
            await s.execute(
                select(Company.provider_slugs).where(col(Company.id).in_(company_ids))
            )
        ).scalars().all()

    for provider_slugs in rows:
        for slug in (provider_slugs or {}).values():
            if isinstance(slug, str) and slug.strip():
                return True
    return False


@router.post("/messages")
async def send_message(
    request: Request,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return {"error": "message is required"}

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
    factory = get_session_factory()
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_factory": factory,
            "profile_id": str(profile.id),
        }
    }

    graph_input: dict = {
        "messages": [{"role": "user", "content": user_message}],
        "profile_id": str(profile.id),
        "resume_md": profile.base_resume_md,
        "profile_updates": {},
    }

    async def stream_response():
        from app.agents.llm_safe import BudgetExhausted

        try:
            from langchain_core.messages import AIMessageChunk

            # Snapshot BEFORE the agent runs so we can detect mutations.
            before = await _profile_updated_at(factory, profile.id)

            async for chunk in graph.astream(
                graph_input,
                config,
                stream_mode="messages",
            ):
                if not (isinstance(chunk, tuple) and len(chunk) == 2):
                    continue
                msg, _metadata = chunk
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

            # AFTER the agent finishes, check for profile mutation.
            after = await _profile_updated_at(factory, profile.id)
            if before != after:
                payload = {
                    "profile_mutated": True,
                    "search_startable": await _profile_can_start_search(factory, profile.id),
                }
                yield f"event: meta\ndata: {json.dumps(payload)}\n\n"
        except BudgetExhausted as exc:
            await log.awarning("chat.budget_exhausted", resumes_at=exc.resumes_at.isoformat())
            payload = {
                "error": "budget_exhausted",
                "resumes_at": exc.resumes_at.isoformat(),
            }
            yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            await log.aexception("chat.stream_error", error=str(e))
            yield f"data: {json.dumps({'error': 'Stream error'})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
