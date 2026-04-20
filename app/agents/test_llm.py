from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel

_DEFAULT_RESPONSES: dict[str, list[str]] = {
    "onboarding": [
        "Hi! Tell me about the role you're looking for.",
        '{"target_title": "Software Engineer", "location": "Remote"}',
    ],
    "matching": [
        '{"score": 75, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}',
    ],
    "generation": [
        "Tailored resume content here.",
        "Tailored cover letter content here.",
    ],
    "resume_extraction": [
        '{"name": "Test User", "skills": ["Python"], "work_experience": []}',
    ],
}


class ToolCapableFakeLLM(FakeListChatModel):
    """FakeListChatModel that accepts bind_tools() without raising NotImplementedError."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeLLM":
        return self


def get_fake_llm(purpose: str = "matching") -> ToolCapableFakeLLM:
    responses = _DEFAULT_RESPONSES.get(purpose, ["fake response"])
    return ToolCapableFakeLLM(responses=responses)
