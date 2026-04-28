"""Cover-letter generation agent. Linear; sync; no checkpointer."""

import structlog
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
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
    doc_type: str
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
    document: GeneratedDoc | None


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


COVER_LETTER_PROMPT = """\
You are writing a tailored cover letter for the candidate below applying to the job below.

CANDIDATE PROFILE:
{profile_text}

CANDIDATE BASE RESUME:
{base_resume_md}

JOB:
Title: {job_title}
Company: {job_company}
Description:
{job_description}

Write a 100–140 word cover letter in Markdown. Be punchy and direct — hiring
managers spend under 30 seconds on each letter. Address it to the hiring team
at the company. Reference 1–2 specific candidate accomplishments that map to
the most important job requirements. Skip generic openers like "I am writing
to express interest" and avoid filler phrases."""


async def _load_context(state: GenerationState) -> GenerationState:
    return {**state, "job_description": truncate_description(state["job_description"])}


async def _generate_cover_letter(state: GenerationState) -> GenerationState:
    llm = get_llm()
    prompt = COVER_LETTER_PROMPT.format(
        profile_text=state["profile_text"],
        base_resume_md=state["base_resume_md"] or "(none provided)",
        job_title=state["job_title"],
        job_company=state["job_company"],
        job_description=state["job_description"],
    )
    result = await safe_ainvoke(llm, [HumanMessage(content=prompt)])
    text = _extract_text(result)
    if not text or len(text) < 30:
        raise GenerationOutputError("cover letter generation returned empty/short text")
    settings = get_settings()
    return {
        **state,
        "document": {
            "doc_type": "cover_letter",
            "content_md": text,
            "generation_model": settings.llm_generation_model,
            "structured_content": None,
        },
    }


async def _finalize(state: GenerationState) -> GenerationState:
    return state


def build_graph():
    g = StateGraph(GenerationState)
    g.add_node("load_context", _load_context)
    g.add_node("generate_cover_letter", _generate_cover_letter)
    g.add_node("finalize", _finalize)
    g.set_entry_point("load_context")
    g.add_edge("load_context", "generate_cover_letter")
    g.add_edge("generate_cover_letter", "finalize")
    g.add_edge("finalize", END)
    return g.compile()
