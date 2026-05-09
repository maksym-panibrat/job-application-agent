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
from sqlalchemy import func
from sqlmodel import select
from typing_extensions import TypedDict

from app.agents.llm_safe import safe_ainvoke
from app.config import get_settings
from app.models.company import Company
from app.models.user_profile import UserProfile
from app.services import company_resolver, profile_service

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a job application assistant helping a user build their profile.

Your goals:
1. Collect their first and last name separately if not already known.
2. Learn their target roles and seniority level.
3. Learn their location — this is REQUIRED to start job search:
   - Ask for a city or metro area (e.g. "San Francisco Bay Area", "New York", "Austin")
   - OR confirm they want remote-only positions (set remote_ok=true, leave target_locations empty)
   - A vague answer like "open to anything" is not enough — pin down a location or remote-only
4. Learn which **companies** the user wants to follow — REQUIRED. Job sourcing
   is built around the companies the user explicitly tracks. Ask for company
   names as the user would say them (e.g. "Stripe", "Linear", "ByteDance").
   Examples to suggest if the user is unsure: stripe, anthropic, datadog,
   figma, notion, vercel, airtable, linear. Save them as
   `target_companies: ["Stripe", "Linear", ...]` (a flat list of display
   names — the backend resolves each to the right ATS automatically).
   Confirm any company that is unfamiliar.
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
  - target_companies contains at least one company.

A profile that satisfies only the location gate but has zero followed
companies will produce zero job matches forever — finish the company ask
before wrapping up.

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

    company_names = data.get("target_company_names") or []
    lines = [
        "## Current Profile (ground truth from the database)",
        f"- full_name: {_val(data.get('full_name'))}",
        f"- target_roles: {_val(data.get('target_roles'))}",
        f"- seniority: {_val(data.get('seniority'))}",
        f"- target_locations: {_val(data.get('target_locations'))}",
        f"- remote_ok: {_val(data.get('remote_ok'))}",
        f"- search_keywords: {_val(data.get('search_keywords'))}",
        f"- target_companies: {_val(company_names)}",
    ]
    return "\n".join(lines)


async def persist_inferred_companies(profile, names: list[str], session) -> list[str]:
    """Resolve each company name via the resolver and append to
    profile.target_company_ids. Returns the list of canonical names that
    resolved successfully.

    Names that fail to resolve are logged and skipped — onboarding does not
    block on them; the agent's transcript can mention which were dropped.
    """
    resolved_ids: list[uuid.UUID] = list(profile.target_company_ids or [])
    resolved_names: list[str] = []
    for name in names:
        try:
            company = await company_resolver.resolve(name, session)
        except company_resolver.FanoutTimeoutError:
            await log.awarning("onboarding.company_resolve_timeout", name=name)
            continue
        if company is None:
            await log.awarning("onboarding.company_unresolved", name=name)
            continue
        if company.id not in resolved_ids:
            resolved_ids.append(company.id)
            resolved_names.append(company.canonical_name)
    profile.target_company_ids = resolved_ids
    session.add(profile)
    await session.commit()
    return resolved_names


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
        company_ids = list(profile.target_company_ids or [])
        company_names: list[str] = []
        if company_ids:
            rows = (
                (await session.execute(select(Company).where(Company.id.in_(company_ids))))
                .scalars()
                .all()
            )
            company_names = [r.canonical_name for r in rows]
        return {
            "full_name": profile.full_name,
            "target_roles": list(profile.target_roles or []),
            "seniority": profile.seniority,
            "target_locations": list(profile.target_locations or []),
            "remote_ok": profile.remote_ok,
            "search_keywords": list(profile.search_keywords or []),
            "target_company_ids": company_ids,
            "target_company_names": company_names,
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


@tool
async def list_curated_companies(config: RunnableConfig) -> str:
    """Return the curated company catalog as a JSON array.
    Each entry has: canonical_name (str) and tags (list of strings).
    Call this when the user asks for company suggestions or to see
    what companies are available. Prefer suggesting from this list;
    you may suggest off-list names too — those get resolved against
    live ATS boards but won't have tags to reason over."""
    db_factory = config["configurable"]["db_factory"]
    async with db_factory() as session:
        rows = (
            await session.execute(
                select(Company.canonical_name, Company.tags)
                .where(Company.is_curated)
                .order_by(func.lower(Company.canonical_name))
            )
        ).all()
    payload = [{"canonical_name": r.canonical_name, "tags": list(r.tags)} for r in rows]
    return json.dumps(payload)


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
        portfolio_url (str),
        target_companies (list of display names, e.g.
        ["Stripe", "Linear", "ByteDance"] — backend resolves each automatically),
        skills (list of {name, category, proficiency, years}),
        work_experiences (list of {company, title, start_date (YYYY-MM-DD), end_date,
        description_md, technologies (list)}).
        """
        return f"Profile update queued: {updates}"

    tools = [save_profile_updates, list_curated_companies]
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

                # target_companies is routed through company_resolver to map
                # display names to Company.id; never written to the deprecated
                # target_company_slugs blob anymore.
                companies_payload = updates.pop("target_companies", None)

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

                if companies_payload:
                    # Defensive: coerce single string to a list
                    if isinstance(companies_payload, str):
                        companies_payload = [companies_payload]
                    if isinstance(companies_payload, list):
                        names = [
                            str(n) for n in companies_payload if isinstance(n, str) and n.strip()
                        ]
                        if names:
                            profile = await session.get(UserProfile, profile_uuid)
                            if profile is not None:
                                try:
                                    await persist_inferred_companies(profile, names, session)
                                except Exception as exc:
                                    await log.awarning(
                                        "onboarding.process_tool_results.company_resolve_failed",
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
