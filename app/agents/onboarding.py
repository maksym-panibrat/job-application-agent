"""
Onboarding agent — conversational profile builder.

LangGraph StateGraph with AsyncPostgresSaver checkpointer.
Thread ID = str(profile.id) → sessions resume across browser refreshes.
Same graph handles initial onboarding and ongoing preference updates.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.config import get_settings
from app.services import profile_service

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a job application assistant helping a user build their profile.

Your goals:
1. Learn their target roles and seniority level
2. Learn their location — this is REQUIRED to start job search:
   - Ask for a city or metro area (e.g. "San Francisco Bay Area", "New York", "Austin")
   - OR confirm they want remote-only positions (set remote_ok=true, leave target_locations empty)
   - A vague answer like "open to anything" is not enough — pin down a location or remote-only
3. Understand their key skills and experience highlights they want to emphasize
4. Note any companies or industries to exclude
5. Confirm their contact info (LinkedIn, GitHub, portfolio)

Ask one or two questions at a time. Be conversational and concise.
When you have enough information to update the profile, call the `save_profile_updates` tool.
You can call it multiple times as you learn more.

Do not consider the profile search-ready until target_locations is set OR remote_ok is true.

Once the profile feels complete, summarize what you've captured and tell the user they can
update preferences anytime by chatting here."""

PROFILE_SCALAR_FIELDS = frozenset({
    "target_roles", "seniority", "target_locations", "remote_ok",
    "search_keywords", "full_name", "email", "phone",
    "linkedin_url", "github_url", "portfolio_url",
})


class OnboardingState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    profile_id: str
    profile_updates: dict
    resume_md: str | None


def get_llm():
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm
        return get_fake_llm("onboarding")
    return ChatGoogleGenerativeAI(
        model=settings.llm_generation_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )


def build_graph(checkpointer: AsyncPostgresSaver) -> StateGraph:
    @tool
    def save_profile_updates(updates: str) -> str:
        """
        Save profile updates from the conversation.
        Pass a JSON string with any subset of these fields:
        target_roles (list), seniority (str), target_locations (list),
        remote_ok (bool), search_keywords (list), full_name (str),
        email (str), phone (str), linkedin_url (str), github_url (str),
        portfolio_url (str), skills (list of {name, category, proficiency, years}),
        work_experiences (list of {company, title, start_date (YYYY-MM-DD), end_date,
        description_md, technologies (list)}).
        """
        return f"Profile update queued: {updates}"

    tools = [save_profile_updates]
    llm = get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    async def agent_node(state: OnboardingState, config: RunnableConfig) -> dict:
        messages = list(state["messages"])
        resume_md = state.get("resume_md")

        system_content = SYSTEM_PROMPT
        if resume_md:
            system_content += f"\n\n## User's Current Resume\n{resume_md}"

        # Always use a fresh system message (strip any stale checkpointed ones)
        messages = [m for m in messages if not isinstance(m, SystemMessage)]
        messages = [SystemMessage(content=system_content)] + messages

        result = await llm.ainvoke(messages)
        return {"messages": [result]}

    async def process_tool_results(state: OnboardingState, config: RunnableConfig) -> dict:
        """Persist save_profile_updates tool calls to the database."""
        profile_id_str = state.get("profile_id")
        if not profile_id_str:
            return {}

        db_factory = config.get("configurable", {}).get("db_factory")
        if db_factory is None:
            await log.awarning("onboarding.process_tool_results.no_db_factory")
            return {}

        # Find the most recent AIMessage that has tool calls
        ai_msg: AIMessage | None = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and msg.tool_calls:
                ai_msg = msg
                break

        if not ai_msg:
            return {}

        profile_uuid = uuid.UUID(profile_id_str)

        async with db_factory() as session:
            for tc in ai_msg.tool_calls:
                if tc["name"] != "save_profile_updates":
                    continue

                raw_updates = tc["args"].get("updates", "")

                # Handle both str (JSON string) and dict (some models pass parsed args)
                if isinstance(raw_updates, dict):
                    updates = raw_updates
                else:
                    try:
                        updates = json.loads(raw_updates)
                    except (json.JSONDecodeError, TypeError):
                        await log.awarning(
                            "onboarding.process_tool_results.invalid_json",
                            raw=str(raw_updates)[:200],
                        )
                        continue

                if not isinstance(updates, dict):
                    continue

                # Separate nested entities from flat profile fields
                skills = list(updates.pop("skills", None) or [])
                experiences = list(updates.pop("work_experiences", None) or [])

                # Update flat profile fields (only recognised fields)
                flat = {k: v for k, v in updates.items() if k in PROFILE_SCALAR_FIELDS}
                if flat:
                    try:
                        await profile_service.update_profile(profile_uuid, flat, session)
                    except Exception as exc:
                        await log.awarning(
                            "onboarding.process_tool_results.update_failed",
                            error=str(exc),
                        )

                for skill in skills:
                    if not isinstance(skill, dict) or not skill.get("name"):
                        continue
                    try:
                        await profile_service.upsert_skill(profile_uuid, skill, session)
                    except Exception as exc:
                        await log.awarning(
                            "onboarding.process_tool_results.skill_failed",
                            name=skill.get("name"),
                            error=str(exc),
                        )

                for exp in experiences:
                    if not isinstance(exp, dict) or not exp.get("company") or not exp.get("title"):
                        continue
                    # Parse date strings to datetime objects
                    exp_copy = dict(exp)
                    for date_field in ("start_date", "end_date"):
                        val = exp_copy.get(date_field)
                        if isinstance(val, str):
                            try:
                                parsed = datetime.fromisoformat(val)
                                if not parsed.tzinfo:
                                    parsed = parsed.replace(tzinfo=UTC)
                                exp_copy[date_field] = parsed
                            except ValueError:
                                exp_copy[date_field] = None
                    if not exp_copy.get("start_date"):
                        continue
                    try:
                        await profile_service.upsert_work_experience(
                            profile_uuid, exp_copy, session
                        )
                    except Exception as exc:
                        await log.awarning(
                            "onboarding.process_tool_results.exp_failed",
                            company=exp.get("company"),
                            error=str(exc),
                        )

        return {}

    def should_continue(state: OnboardingState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    builder = StateGraph(OnboardingState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_node("process_tool_results", process_tool_results)
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "process_tool_results")
    builder.add_edge("process_tool_results", "agent")

    return builder.compile(checkpointer=checkpointer)
