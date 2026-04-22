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
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.agents.llm_safe import safe_ainvoke
from app.agents.matching_agent import truncate_description
from app.config import get_settings

log = structlog.get_logger()


class GenerationOutputError(Exception):
    """LLM returned an unparseable response.

    Raised when the message has tool_calls but no text content, or when the
    content is empty / an unknown block shape after normalization.
    """


class GeneratedDoc(TypedDict):
    doc_type: str  # tailored_resume, cover_letter, custom_answers
    content_md: str
    generation_model: str
    structured_content: dict | None


class GenerationState(TypedDict):
    application_id: str
    profile_text: str
    job_title: str
    job_company: str
    job_description: str
    base_resume_md: str
    custom_questions: list  # list[str] or list[dict] with at least {"label": str}
    documents: Annotated[list[GeneratedDoc], operator.add]
    generation_status: str
    user_decision: dict  # set on resume after interrupt


def _extract_text(result) -> str:
    """Extract plain text from a LangChain chat-model result.

    Accepts either a full LangChain message (with ``content`` and optional
    ``tool_calls`` attributes) or a raw content value (string / list of
    content blocks).

    Raises GenerationOutputError when the response has tool_calls but no text
    content (our text-output prompts never request tool_calls), or when the
    content is empty / unparseable after normalization.
    """
    content = result.content if hasattr(result, "content") else result
    tool_calls = getattr(result, "tool_calls", None)

    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        text = "\n".join(parts).strip()
    else:
        text = str(content or "")

    text = text.strip()
    if not text:
        if tool_calls:
            raise GenerationOutputError(
                f"LLM returned tool_calls ({len(tool_calls)}) with no text content"
            )
        raise GenerationOutputError("LLM returned empty text content")
    return text


def get_llm():
    settings = get_settings()
    if settings.environment == "test":
        from app.agents.test_llm import get_fake_llm

        return get_fake_llm("generation")
    return ChatGoogleGenerativeAI(
        model=settings.llm_generation_model,
        google_api_key=settings.google_api_key.get_secret_value(),
    )


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
    model_name = get_settings().llm_generation_model

    def load_context_node(state: GenerationState) -> dict:
        return {"generation_status": "generating"}

    def route_generation(state: GenerationState) -> list[str]:
        routes = ["generate_resume", "generate_cover_letter"]
        if state.get("custom_questions"):
            routes.append("answer_custom_questions")
        return routes

    async def generate_resume_node(state: GenerationState) -> dict:
        prompt = RESUME_PROMPT.format(
            base_resume_md=state["base_resume_md"][:6000],
            title=state["job_title"],
            company=state["job_company"],
            description=truncate_description(state["job_description"]),
        )
        result = await safe_ainvoke(llm, [HumanMessage(content=prompt)])
        return {
            "documents": [
                {
                    "doc_type": "tailored_resume",
                    "content_md": _extract_text(result),
                    "generation_model": model_name,
                    "structured_content": None,
                }
            ]
        }

    async def generate_cover_letter_node(state: GenerationState) -> dict:
        prompt = COVER_LETTER_PROMPT.format(
            profile_text=state["profile_text"][:3000],
            title=state["job_title"],
            company=state["job_company"],
            description=truncate_description(state["job_description"]),
        )
        result = await safe_ainvoke(llm, [HumanMessage(content=prompt)])
        return {
            "documents": [
                {
                    "doc_type": "cover_letter",
                    "content_md": _extract_text(result),
                    "generation_model": model_name,
                    "structured_content": None,
                }
            ]
        }

    async def answer_custom_questions_node(state: GenerationState) -> dict:
        questions = state.get("custom_questions", [])
        if not questions:
            return {}

        # Support both list[str] and list[dict] with at least {"label": str}
        def _label(q) -> str:
            return q["label"] if isinstance(q, dict) else q

        labels = [_label(q) for q in questions]
        formatted = "\n".join(f"{i + 1}. {lbl}" for i, lbl in enumerate(labels))
        prompt = CUSTOM_QUESTIONS_PROMPT.format(
            profile_text=state["profile_text"][:2000],
            title=state["job_title"],
            company=state["job_company"],
            questions=formatted,
        )
        result = await safe_ainvoke(llm, [HumanMessage(content=prompt)])
        content_md = _extract_text(result)

        # Build structured_content: {question_label: answer_text}
        # Parse the Markdown output for "A: ..." lines following each question block.
        # Accumulate continuation lines until the next **Q:** header.
        structured_content: dict = {}
        current_label: str | None = None
        answer_buffer: list[str] = []

        def _flush_buffer():
            if current_label is not None and answer_buffer:
                structured_content[current_label] = " ".join(answer_buffer).strip()

        for line in content_md.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Q:") and stripped.endswith("**"):
                _flush_buffer()
                answer_buffer = []
                q_text = stripped[4:-2].strip()
                # Match back to the original label (case-insensitive prefix match)
                for lbl in labels:
                    if lbl.lower() in q_text.lower() or q_text.lower() in lbl.lower():
                        current_label = lbl
                        break
                else:
                    current_label = q_text
            elif stripped.startswith("A:") and current_label is not None:
                answer_buffer = [stripped[2:].strip()]
            elif stripped and current_label is not None and answer_buffer:
                answer_buffer.append(stripped)

        _flush_buffer()

        return {
            "documents": [
                {
                    "doc_type": "custom_answers",
                    "content_md": content_md,
                    "generation_model": model_name,
                    "structured_content": structured_content or None,
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
        """Terminal node — graph END follows.

        Only mutates the graph's in-memory state. The DB ``generation_status``
        is owned by the service layer (``generate_materials`` and
        ``resume_generation`` write it exactly once, after ``graph.ainvoke``
        returns). Writing here too would race with those callers and re-read
        a stale ``app`` snapshot afterwards.
        """
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
