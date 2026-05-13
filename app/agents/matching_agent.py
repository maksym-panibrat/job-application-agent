"""Matching agent scoring primitives."""

import operator
from typing import Annotated

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.agents.llm_safe import safe_ainvoke
from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import UserProfile

log = structlog.get_logger()

MAX_JOB_DESC_CHARS = 20000


def truncate_description(desc: str, max_chars: int = MAX_JOB_DESC_CHARS) -> str:
    if not desc or len(desc) <= max_chars:
        return desc or ""
    return desc[:max_chars] + "\n\n[Description truncated]"


class ScoreResult(BaseModel):
    application_id: str
    score: float | None  # 0.0 – 1.0; None signals scoring was skipped (retry next sync)
    summary: str = ""  # ≤12 words; UI display
    rationale: str  # ≤20 words; audit only
    strengths: list[str]  # 1-3 JD-met items
    gaps: list[str]  # 1-3 missing/weak items

    @field_validator("strengths", "gaps", mode="before")
    @classmethod
    def coerce_to_list(cls, v: object) -> list[str]:
        """Flash sometimes returns bullet-point strings instead of JSON arrays."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            items = [line.lstrip("-•* \t").strip() for line in v.splitlines() if line.strip()]
            return [item for item in items if item]
        return []


class JobContext(TypedDict):
    application_id: str
    title: str
    company: str
    location: str | None
    workplace_type: str | None
    description: str


class MatchState(TypedDict):
    profile_id: str
    profile_text: str
    jobs: list[JobContext]
    scores: Annotated[list[ScoreResult], operator.add]


class SingleJobState(TypedDict):
    profile_text: str
    job: JobContext


def get_llm():
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm

        return get_fake_llm("matching")
    return ChatGoogleGenerativeAI(
        model=settings.llm_matching_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )


SCORING_SYSTEM_PROMPT = """\
Score how the candidate profile matches the job (0.0-1.0).

Grading:
- 0.9-1.0: meets all required + most preferred
- 0.7-0.89: meets all required, some preferred gaps
- 0.5-0.69: meets most required, notable gaps
- 0.3-0.49: meets some required, major gaps
- 0.0-0.29: fundamental mismatch

Location:
- JD location is in candidate locations OR (JD remote AND candidate remote): not a gap.
- Otherwise: hard gap, e.g., "Onsite Seattle, candidate based in CA".
- Never say "may require clarification" or "depends". Decide.

Output (call record_score):
- summary: <=12 words. The JOB: level, stack, mode. No prose.
- strengths: 1-3 JD requirements the candidate meets. <=8 words each. No filler.
- gaps: 1-3 weak/missing JD requirements. <=8 words each. No filler.
- rationale: <=20 words. Why this score (audit)."""


SCORING_USER_TEMPLATE = """\
PROFILE:
{profile_text}

JOB: {title} @ {company}
Location: {location} · {workplace_type}
{description}"""


@tool
def record_score(
    score: float,
    summary: str,
    rationale: str,
    strengths: list[str],
    gaps: list[str],
) -> str:
    """Record the match score for this job application."""
    return "Score recorded"


async def score_job_context(*, profile_text: str, job: JobContext) -> ScoreResult:
    user_prompt = SCORING_USER_TEMPLATE.format(
        profile_text=profile_text,
        title=job["title"],
        company=job["company"],
        location=job.get("location") or "unspecified",
        workplace_type=job.get("workplace_type") or "unspecified",
        description=truncate_description(job["description"]),
    )
    run_config = {
        "run_name": f"score-{job['company'][:20]}-{job['title'][:30]}",
        "metadata": {"application_id": job["application_id"]},
    }
    llm = get_llm().bind_tools([record_score], tool_choice="record_score")
    result = await safe_ainvoke(
        llm,
        [
            SystemMessage(content=SCORING_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ],
        config=run_config,
    )
    tool_call = result.tool_calls[0] if result.tool_calls else {}
    args = tool_call.get("args", {}) if tool_call else {}
    return ScoreResult(
        application_id=job["application_id"],
        score=float(args.get("score", 0.0)),
        summary=args.get("summary", ""),
        rationale=args.get("rationale", ""),
        strengths=args.get("strengths", []),
        gaps=args.get("gaps", []),
    )


async def score_one(application: Application, session: AsyncSession) -> dict:
    job = await session.get(Job, application.job_id)
    profile = await session.get(UserProfile, application.profile_id)
    if job is None or profile is None:
        raise ValueError("missing job or profile")

    from app.services.match_service import format_profile_text
    from app.services.profile_service import get_skills, get_work_experiences

    skills = await get_skills(profile.id, session)
    experiences = await get_work_experiences(profile.id, session)
    profile_text = format_profile_text(profile, skills, experiences)
    score = await score_job_context(
        profile_text=profile_text,
        job={
            "application_id": str(application.id),
            "title": job.title,
            "company": job.company_name,
            "location": job.location,
            "workplace_type": job.workplace_type,
            "description": job.description or job.description_raw or "",
        },
    )
    return score.model_dump()


def build_graph() -> StateGraph:
    def load_context_node(state: MatchState) -> dict:
        return {}

    def fan_out(state: MatchState) -> list[Send]:
        return [
            Send("score_job", {"profile_text": state["profile_text"], "job": job})
            for job in state["jobs"]
        ]

    async def score_job_node(state: SingleJobState) -> dict:
        score_result = await score_job_context(
            profile_text=state["profile_text"], job=state["job"]
        )
        return {"scores": [score_result]}

    async def persist_results_node(state: MatchState) -> dict:
        return {}

    builder = StateGraph(MatchState)
    builder.add_node("load_context", load_context_node)
    builder.add_node("score_job", score_job_node)
    builder.add_node("persist_results", persist_results_node)
    builder.set_entry_point("load_context")
    builder.add_conditional_edges("load_context", fan_out, ["score_job"])
    builder.add_edge("score_job", "persist_results")
    builder.add_edge("persist_results", END)

    return builder.compile()
