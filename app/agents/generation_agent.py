"""
Generation agent — LangGraph with parallel edges + conditional routing + interrupt.

Graph: load_context → [generate_resume ‖ generate_cover_letter ‖ answer_custom_questions?]
       → review (interrupt) → finalize

The interrupt pauses the graph, checkpoints to Postgres, and resumes when the user
approves/edits in the UI — potentially hours/days later. This is why LangGraph is used.
"""

import operator
from typing import Annotated

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.agents.matching_agent import truncate_description
from app.config import get_settings

log = structlog.get_logger()


class GeneratedDoc(TypedDict):
    doc_type: str  # tailored_resume, cover_letter, custom_answers
    content_md: str
    generation_model: str


class GenerationState(TypedDict):
    application_id: str
    profile_text: str
    job_title: str
    job_company: str
    job_description: str
    base_resume_md: str
    custom_questions: list[str]
    documents: Annotated[list[GeneratedDoc], operator.add]
    generation_status: str
    user_decision: dict  # set on resume after interrupt


def _extract_text(content) -> str:
    """Normalize LangChain message content to a plain string.

    ChatAnthropic may return a list of content blocks (e.g. text + tool_use) for
    newer models. Extract and join the text blocks so we always store a clean string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()
    return str(content)


def get_llm() -> ChatAnthropic:
    settings = get_settings()
    kwargs: dict = dict(
        model=settings.claude_model,
        api_key=settings.anthropic_api_key.get_secret_value(),
    )
    if settings.anthropic_base_url:
        kwargs["anthropic_api_url"] = settings.anthropic_base_url
    return ChatAnthropic(**kwargs)


RESUME_PROMPT = """\
You are an expert resume writer. Tailor the candidate's base resume for this specific job.

BASE RESUME:
{base_resume_md}

TARGET JOB:
Title: {title}
Company: {company}
Description:
{description}

Instructions:
- Keep the same structure as the base resume
- Emphasize experience and skills most relevant to this role
- Adjust language to mirror the job description's terminology
- Do not fabricate experience or skills
- Output only the tailored resume in Markdown format"""

COVER_LETTER_PROMPT = """\
Write a concise, compelling cover letter for this job application.

CANDIDATE PROFILE:
{profile_text}

TARGET JOB:
Title: {title}
Company: {company}
Description:
{description}

Instructions:
- 3–4 paragraphs max
- Opening: express genuine interest in the specific role/company
- Middle: highlight 2–3 most relevant experiences
- Closing: call to action
- Tone: professional but conversational
- Output only the cover letter in Markdown format"""

CUSTOM_QUESTIONS_PROMPT = """\
Answer these custom application questions for the candidate.

CANDIDATE PROFILE:
{profile_text}

JOB: {title} at {company}

QUESTIONS:
{questions}

For each question, provide a concise, specific answer (2–4 sentences).
Format as:
**Q: [question]**
A: [answer]
"""


def build_graph(checkpointer: AsyncPostgresSaver) -> StateGraph:
    llm = get_llm()
    model_name = get_settings().claude_model

    def load_context_node(state: GenerationState) -> dict:
        return {"generation_status": "generating"}

    def route_generation(state: GenerationState) -> list[str]:
        routes = ["generate_resume", "generate_cover_letter"]
        if state.get("custom_questions"):
            routes.append("answer_custom_questions")
        return routes

    def generate_resume_node(state: GenerationState) -> dict:
        prompt = RESUME_PROMPT.format(
            base_resume_md=state["base_resume_md"][:6000],
            title=state["job_title"],
            company=state["job_company"],
            description=truncate_description(state["job_description"]),
        )
        result = llm.invoke([HumanMessage(content=prompt)])
        return {
            "documents": [
                {
                    "doc_type": "tailored_resume",
                    "content_md": _extract_text(result.content),
                    "generation_model": model_name,
                }
            ]
        }

    def generate_cover_letter_node(state: GenerationState) -> dict:
        prompt = COVER_LETTER_PROMPT.format(
            profile_text=state["profile_text"][:3000],
            title=state["job_title"],
            company=state["job_company"],
            description=truncate_description(state["job_description"]),
        )
        result = llm.invoke([HumanMessage(content=prompt)])
        return {
            "documents": [
                {
                    "doc_type": "cover_letter",
                    "content_md": _extract_text(result.content),
                    "generation_model": model_name,
                }
            ]
        }

    def answer_custom_questions_node(state: GenerationState) -> dict:
        questions = state.get("custom_questions", [])
        if not questions:
            return {}
        formatted = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
        prompt = CUSTOM_QUESTIONS_PROMPT.format(
            profile_text=state["profile_text"][:2000],
            title=state["job_title"],
            company=state["job_company"],
            questions=formatted,
        )
        result = llm.invoke([HumanMessage(content=prompt)])
        return {
            "documents": [
                {
                    "doc_type": "custom_answers",
                    "content_md": _extract_text(result.content),
                    "generation_model": model_name,
                }
            ]
        }

    async def save_documents_node(state: GenerationState) -> dict:
        """Persist generated documents to DB before interrupt."""
        from app.database import get_session_factory
        from app.services.application_service import save_documents

        factory = get_session_factory()
        async with factory() as session:
            await save_documents(state["application_id"], state["documents"], session)
        return {}

    def review_node(state: GenerationState) -> dict:
        """Interrupt — pauses graph, waits for user to approve or request regeneration."""
        decision = interrupt(
            {
                "application_id": state["application_id"],
                "documents": state["documents"],
                "message": "Documents ready for review",
            }
        )
        return {"user_decision": decision, "generation_status": "ready"}

    def route_after_review(state: GenerationState) -> str:
        decision = state.get("user_decision", {})
        if decision.get("regenerate"):
            return "load_context"
        return "finalize"

    def finalize_node(state: GenerationState) -> dict:
        return {"generation_status": "ready"}

    builder = StateGraph(GenerationState)
    builder.add_node("load_context", load_context_node)
    builder.add_node("generate_resume", generate_resume_node)
    builder.add_node("generate_cover_letter", generate_cover_letter_node)
    builder.add_node("answer_custom_questions", answer_custom_questions_node)
    builder.add_node("save_documents", save_documents_node)
    builder.add_node("review", review_node)
    builder.add_node("finalize", finalize_node)

    builder.set_entry_point("load_context")
    builder.add_conditional_edges(
        "load_context",
        route_generation,
        ["generate_resume", "generate_cover_letter", "answer_custom_questions"],
    )
    builder.add_edge("generate_resume", "save_documents")
    builder.add_edge("generate_cover_letter", "save_documents")
    builder.add_edge("answer_custom_questions", "save_documents")
    builder.add_edge("save_documents", "review")
    builder.add_conditional_edges(
        "review", route_after_review, {"load_context": "load_context", "finalize": "finalize"}
    )
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer, interrupt_before=["review"])
