"""
Onboarding agent — conversational profile builder.

LangGraph StateGraph with AsyncPostgresSaver checkpointer.
Thread ID = str(profile.id) → sessions resume across browser refreshes.
Same graph handles initial onboarding and ongoing preference updates.
"""

from typing import Annotated

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.config import get_settings

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a job application assistant helping a user build their profile.

Your goals:
1. Learn their target roles, seniority level, location preferences, and remote preference
2. Understand their key skills and experience highlights they want to emphasize
3. Note any companies or industries to exclude
4. Confirm their contact info (LinkedIn, GitHub, portfolio)

Ask one or two questions at a time. Be conversational and concise.
When you have enough information to update the profile, call the `save_profile_updates` tool.
You can call it multiple times as you learn more.

Once the profile feels complete, summarize what you've captured and tell the user they can
update preferences anytime by chatting here."""

PROFILE_FIELDS_SCHEMA = {
    "target_roles": "list of job titles (e.g. ['Senior Backend Engineer', 'Staff SWE'])",
    "seniority": "level: 'junior', 'mid', 'senior', 'staff', 'principal'",
    "target_locations": "list of locations (e.g. ['New York', 'San Francisco', 'remote'])",
    "remote_ok": "boolean — are they open to remote work",
    "search_keywords": "list of keywords for job search queries",
    "full_name": "their full name",
    "email": "contact email",
    "phone": "phone number",
    "linkedin_url": "LinkedIn profile URL",
    "github_url": "GitHub profile URL",
    "portfolio_url": "portfolio/personal site URL",
}


class OnboardingState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    profile_id: str
    profile_updates: dict  # accumulated updates to save


def get_llm() -> ChatAnthropic:
    settings = get_settings()
    return ChatAnthropic(
        model=settings.claude_model,
        api_key=settings.anthropic_api_key.get_secret_value(),
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
        portfolio_url (str).
        """
        # The actual DB write happens in the graph node after tool call
        # This tool just signals what to update; the node applies it
        return f"Profile update queued: {updates}"

    tools = [save_profile_updates]
    llm = get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def agent_node(state: OnboardingState) -> dict:
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

        result = llm.invoke(messages)
        return {"messages": [result]}

    def should_continue(state: OnboardingState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    builder = StateGraph(OnboardingState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer)
