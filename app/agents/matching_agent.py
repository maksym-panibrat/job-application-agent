"""
Matching agent — LangGraph StateGraph with Send-based fan-out.

Graph: load_context → fan_out (Send) → score_job (×N parallel) → persist_results

Uses Flash for cost efficiency. Prompt is split into a stable SystemMessage
(grading rubric + output rules — Gemini implicit cache prefix) and a
per-call HumanMessage carrying the profile and the job-specific content.
"""

import asyncio
import operator
from typing import Annotated

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, field_validator
from typing_extensions import TypedDict

from app.agents.llm_safe import BudgetExhausted, safe_ainvoke
from app.config import get_settings

log = structlog.get_logger()

MAX_JOB_DESC_CHARS = 8000


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


def build_graph() -> StateGraph:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.matching_max_concurrency)

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

    tools = [record_score]
    llm = get_llm().bind_tools(tools, tool_choice="record_score")

    def load_context_node(state: MatchState) -> dict:
        return {}

    def fan_out(state: MatchState) -> list[Send]:
        return [
            Send("score_job", {"profile_text": state["profile_text"], "job": job})
            for job in state["jobs"]
        ]

    async def score_job_node(state: SingleJobState) -> dict:
        job = state["job"]
        user_prompt = SCORING_USER_TEMPLATE.format(
            profile_text=state["profile_text"],
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
        # Retry loop: handle transient API rate limit errors with backoff.
        # BudgetExhausted (monthly quota) is NOT retried — it is caught and
        # converted to score=None so the entire matching run is not aborted.
        # Falls back to score=None after exhausting retries for transient errors.
        backoffs = [10, 30]
        for attempt, backoff in enumerate([0] + backoffs):
            if backoff:
                await asyncio.sleep(backoff)
            async with semaphore:
                await asyncio.sleep(0.5)  # throttle: ~6 req/s per slot
                try:
                    result = await safe_ainvoke(
                        llm,
                        [
                            SystemMessage(content=SCORING_SYSTEM_PROMPT),
                            HumanMessage(content=user_prompt),
                        ],
                        config=run_config,
                    )
                    break
                except BudgetExhausted:
                    log.warning("match.budget_exhausted_skip", title=job["title"])
                    return {
                        "scores": [
                            ScoreResult(
                                application_id=job["application_id"],
                                score=None,
                                summary="",
                                rationale="Skipped: LLM quota exhausted",
                                strengths=[],
                                gaps=[],
                            )
                        ]
                    }
                except Exception as exc:
                    is_rate_limit = "429" in str(exc) or "rate_limit" in str(exc).lower()
                    if is_rate_limit and attempt < len(backoffs):
                        continue
                    if is_rate_limit:
                        log.warning(
                            "match.rate_limit_skip",
                            title=job["title"],
                            attempts=attempt + 1,
                        )
                        return {
                            "scores": [
                                ScoreResult(
                                    application_id=job["application_id"],
                                    score=None,
                                    summary="",
                                    rationale="Skipped: API rate limit exceeded after retries",
                                    strengths=[],
                                    gaps=[],
                                )
                            ]
                        }
                    raise

        tool_call = result.tool_calls[0] if result.tool_calls else {}
        args = tool_call.get("args", {}) if tool_call else {}

        score_result = ScoreResult(
            application_id=job["application_id"],
            score=float(args.get("score", 0.0)),
            summary=args.get("summary", ""),
            rationale=args.get("rationale", ""),
            strengths=args.get("strengths", []),
            gaps=args.get("gaps", []),
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
