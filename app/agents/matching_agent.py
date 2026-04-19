"""
Matching agent — LangGraph StateGraph with Send-based fan-out.

Graph: load_context → fan_out (Send) → score_job (×N parallel) → persist_results

Uses Haiku for cost efficiency. cache_control on profile_text (shared across all scorings).
"""

import operator
import threading
from typing import Annotated

import structlog
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, field_validator
from typing_extensions import TypedDict

from app.config import get_settings

log = structlog.get_logger()

MAX_JOB_DESC_CHARS = 8000


def truncate_description(desc: str, max_chars: int = MAX_JOB_DESC_CHARS) -> str:
    if not desc or len(desc) <= max_chars:
        return desc or ""
    return desc[:max_chars] + "\n\n[Description truncated]"


class ScoreResult(BaseModel):
    application_id: str
    score: float  # 0.0 – 1.0
    rationale: str
    strengths: list[str]
    gaps: list[str]

    @field_validator("strengths", "gaps", mode="before")
    @classmethod
    def coerce_to_list(cls, v: object) -> list[str]:
        """Haiku sometimes returns bullet-point strings instead of JSON arrays."""
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


SCORING_PROMPT = """\
You are a job application screener. Rate how well this candidate profile matches the job.

CANDIDATE PROFILE:
{profile_text}

JOB POSTING:
Title: {title}
Company: {company}
Description:
{description}

Score the match from 0.0 to 1.0 (1.0 = perfect match).
Call the record_score tool with your assessment."""


def build_graph() -> StateGraph:
    settings = get_settings()
    semaphore = threading.Semaphore(settings.matching_max_concurrency)

    @tool
    def record_score(
        score: float,
        rationale: str,
        strengths: list[str],
        gaps: list[str],
    ) -> str:
        """Record the match score for this job application."""
        return "Score recorded"

    tools = [record_score]
    llm = get_llm().bind_tools(tools, tool_choice="record_score")

    def load_context_node(state: MatchState) -> dict:
        # profile_text and jobs are already in state (provided at invocation)
        return {}

    def fan_out(state: MatchState) -> list[Send]:
        return [
            Send("score_job", {"profile_text": state["profile_text"], "job": job})
            for job in state["jobs"]
        ]

    def score_job_node(state: SingleJobState) -> dict:
        import time

        job = state["job"]
        prompt = SCORING_PROMPT.format(
            profile_text=state["profile_text"],
            title=job["title"],
            company=job["company"],
            description=truncate_description(job["description"]),
        )
        run_config = {
            "run_name": f"score-{job['company'][:20]}-{job['title'][:30]}",
            "metadata": {"application_id": job["application_id"]},
        }
        # Retry loop: handle API rate limit errors with backoff.
        # Falls back to score=0.0 (auto_rejected) after exhausting retries so
        # a single 429 doesn't crash the entire fan-out.
        backoffs = [10, 30]
        for attempt, backoff in enumerate([0] + backoffs):
            if backoff:
                time.sleep(backoff)
            with semaphore:
                time.sleep(1.5)  # throttle: ~2 req/s per slot → ~8k tokens/min
                try:
                    result = llm.invoke([HumanMessage(content=prompt)], config=run_config)
                    break
                except Exception as exc:
                    is_rate_limit = "rate_limit" in str(exc).lower() or "429" in str(exc)
                    if is_rate_limit and attempt < len(backoffs):
                        continue
                    if is_rate_limit:
                        log.warning(
                            "match.rate_limit_skip",
                            title=job["title"],
                            attempts=attempt + 1,
                        )
                        return {"scores": [ScoreResult(
                            application_id=job["application_id"],
                            score=0.0,
                            rationale="Skipped: API rate limit exceeded after retries",
                            strengths=[],
                            gaps=[],
                        )]}
                    raise

        tool_call = result.tool_calls[0] if result.tool_calls else {}
        args = tool_call.get("args", {}) if tool_call else {}

        score_result = ScoreResult(
            application_id=job["application_id"],
            score=float(args.get("score", 0.0)),
            rationale=args.get("rationale", ""),
            strengths=args.get("strengths", []),
            gaps=args.get("gaps", []),
        )
        return {"scores": [score_result]}

    async def persist_results_node(state: MatchState) -> dict:
        # Results are persisted by the caller (match_service) after graph completion
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
