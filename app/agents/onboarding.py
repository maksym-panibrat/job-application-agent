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
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from app.agents.llm_safe import safe_ainvoke
from app.config import get_settings
from app.models.user_profile import UserProfile
from app.services import profile_service, slug_registry_service

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a job application assistant helping a user build their profile.

Your goals:
1. Collect their first and last name separately if not already known.
2. Learn their target roles and seniority level.
3. Learn their location — this is REQUIRED to start job search:
   - Ask for a city or metro area (e.g. "San Francisco Bay Area", "New York", "Austin")
   - OR confirm they want remote-only positions (set remote_ok=true, leave target_locations empty)
   - A vague answer like "open to anything" is not enough — pin down a location or remote-only
4. Learn which companies' job boards to follow — REQUIRED. Job sourcing is built
   on Greenhouse public boards, so an empty company list = zero jobs ever. Always
   ask for at least one target company, even if the user did not volunteer any.
   Offer concrete suggestions to make the ask actionable, e.g. stripe, openai,
   anthropic, datadog, figma, notion, vercel, airtable. Store the slugs as
   {"greenhouse": ["slug1", "slug2"]} in target_company_slugs. Slugs are
   lowercase, no spaces (boards.greenhouse.io/{slug}). Confirm any slug that is
   not obvious.
5. Understand their key skills and experience highlights they want to emphasize.
6. Note any companies or industries to exclude.
7. Confirm their contact info (LinkedIn, GitHub, portfolio).

Ask one or two questions at a time. Be conversational and concise.

# Mandatory tool-call discipline

You MUST call the `save_profile_updates` tool BEFORE acknowledging any profile change
in your reply. If the user states a preference, correction, or new value for any
profile field — roles, seniority, location, remote_ok, search_keywords, target
companies, exclusions, or contact info — you MUST invoke `save_profile_updates`
in the same turn that you confirm the change.

NEVER claim a change is saved if no tool call was made in this turn. Phrases like
"I've updated", "I've saved", "I've adjusted", "I've corrected", or "Done" are
forbidden unless the corresponding tool call was actually issued. If you are
unsure what is currently saved, prefer over-saving (re-issue the tool call)
over under-saving — the tool is idempotent.

You can call `save_profile_updates` multiple times as you learn more.

# Search-ready gate

Do not consider the profile search-ready until ALL of the following hold:
  - target_locations is set OR remote_ok is true, AND
  - target_company_slugs.greenhouse contains at least one slug.

A profile that satisfies only the location gate but has zero greenhouse slugs
will produce zero job matches forever — finish the slug ask before wrapping up.

Once the profile is search-ready, summarize what you've captured and tell the
user they can update preferences anytime by chatting here."""

PROFILE_SCALAR_FIELDS = frozenset(
    {
        "target_roles",
        "seniority",
        "target_locations",
        "remote_ok",
        "search_keywords",
        "full_name",
        "email",
        "phone",
        "linkedin_url",
        "github_url",
        "portfolio_url",
        "target_company_slugs",
        "first_name",
        "last_name",
    }
)


class OnboardingState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    profile_id: str
    profile_updates: dict
    resume_md: str | None


def _format_current_profile(data: dict) -> str:
    """Render a compact, LLM-readable snapshot of the current saved profile state.

    Empty/None fields are rendered explicitly as "(none)" so the LLM can tell
    the difference between unset and a value it might have hallucinated saving.
    """

    def _val(v):
        if v is None:
            return "(none)"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) if v else "(none)"
        if isinstance(v, dict):
            return ", ".join(f"{k}={v}" for k, v in v.items()) if v else "(none)"
        if isinstance(v, bool):
            return "true" if v else "false"
        s = str(v).strip()
        return s if s else "(none)"

    greenhouse_slugs = (data.get("target_company_slugs") or {}).get("greenhouse", [])
    lines = [
        "## Current Profile (ground truth from the database)",
        f"- full_name: {_val(data.get('full_name'))}",
        f"- target_roles: {_val(data.get('target_roles'))}",
        f"- seniority: {_val(data.get('seniority'))}",
        f"- target_locations: {_val(data.get('target_locations'))}",
        f"- remote_ok: {_val(data.get('remote_ok'))}",
        f"- search_keywords: {_val(data.get('search_keywords'))}",
        f"- target_company_slugs.greenhouse: {_val(greenhouse_slugs)}",
    ]
    return "\n".join(lines)


async def persist_inferred_slugs(profile, slugs: list[str], session) -> list[str]:
    """Validate each slug against Greenhouse before persisting.

    Returns the list of slugs that survived validation. The profile's
    target_company_slugs["greenhouse"] is replaced with that list and
    committed. Slugs that fail validate_slug (e.g. 404s) are dropped so
    the sync queue never sees a non-existent board.
    """
    valid: list[str] = []
    for s in slugs:
        if await slug_registry_service.validate_slug("greenhouse_board", s, session):
            valid.append(s)
    profile.target_company_slugs = {
        **(profile.target_company_slugs or {}),
        "greenhouse": valid,
    }
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return valid


async def _fetch_profile_snapshot(state: dict, config: RunnableConfig) -> dict | None:
    """Load the profile row referenced by state['profile_id'] and return its
    relevant fields as a plain dict. Returns None when profile_id or db_factory
    is missing (e.g. legacy tests that don't wire either)."""
    profile_id_str = state.get("profile_id")
    if not profile_id_str:
        return None
    db_factory = (config or {}).get("configurable", {}).get("db_factory")
    if db_factory is None:
        return None
    try:
        profile_uuid = uuid.UUID(profile_id_str)
    except (ValueError, TypeError):
        return None

    async with db_factory() as session:
        profile = await session.get(UserProfile, profile_uuid)
        if profile is None:
            return None
        return {
            "full_name": profile.full_name,
            "target_roles": list(profile.target_roles or []),
            "seniority": profile.seniority,
            "target_locations": list(profile.target_locations or []),
            "remote_ok": profile.remote_ok,
            "search_keywords": list(profile.search_keywords or []),
            "target_company_slugs": dict(profile.target_company_slugs or {}),
        }


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
        first_name (str), last_name (str),
        email (str), phone (str), linkedin_url (str), github_url (str),
        portfolio_url (str), target_company_slugs (dict, e.g.
        {"greenhouse": ["stripe", "airbnb"], "lever": [], "ashby": []}),
        skills (list of {name, category, proficiency, years}),
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

        # Inject the live profile snapshot so the LLM has ground truth on every
        # turn — prevents the "I've already saved that" hallucination that drove
        # issue #40. Re-fetch from the DB on every call so newly persisted
        # tool-call updates show up immediately on the next turn.
        profile_data = await _fetch_profile_snapshot(state, config)
        if profile_data is not None:
            system_content += "\n\n" + _format_current_profile(profile_data)

        if resume_md:
            system_content += f"\n\n## User's Current Resume\n{resume_md}"

        # Always use a fresh system message (strip any stale checkpointed ones)
        messages = [m for m in messages if not isinstance(m, SystemMessage)]
        messages = [SystemMessage(content=system_content)] + messages

        result = await safe_ainvoke(llm, messages)
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

                # target_company_slugs needs per-slug validation against
                # Greenhouse before persisting; route it through the dedicated
                # helper instead of letting profile_service.update_profile blob
                # the LLM-inferred dict in unchecked.
                slug_payload = updates.pop("target_company_slugs", None)

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

                if isinstance(slug_payload, dict):
                    inferred = slug_payload.get("greenhouse") or []
                    if isinstance(inferred, list) and inferred:
                        profile = await session.get(UserProfile, profile_uuid)
                        if profile is not None:
                            try:
                                await persist_inferred_slugs(
                                    profile,
                                    [str(s) for s in inferred if isinstance(s, str)],
                                    session,
                                )
                            except Exception as exc:
                                await log.awarning(
                                    "onboarding.process_tool_results.slug_validate_failed",
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
