import json
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

_DEFAULT_RESPONSES: dict[str, list[str]] = {
    "onboarding": [
        "I've saved your profile! Is there anything else you'd like to update?",
        '{"target_title": "Software Engineer", "location": "Remote"}',
    ],
    "matching": [
        '{"score": 0.75, "rationale": "Good match", "strengths": ["Python"], "gaps": ["Go"]}',
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
    """
    FakeListChatModel that:
    - Accepts bind_tools() without raising NotImplementedError
    - Auto-populates tool_calls on the returned AIMessage when tools are bound
      and the response string parses as a valid JSON dict.
    """

    _bound_tool_name: str | None = PrivateAttr(default=None)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ToolCapableFakeLLM":
        if tools:
            first = tools[0]
            self._bound_tool_name = getattr(first, "name", None) or getattr(
                first, "__name__", str(first)
            )
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if not self._bound_tool_name:
            return result
        new_gens = []
        for gen in result.generations:
            content = gen.message.content
            try:
                args = json.loads(content)
                if isinstance(args, dict):
                    new_gens.append(
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": self._bound_tool_name,
                                        "args": args,
                                        "id": "fake-tool-call-0",
                                    }
                                ],
                            )
                        )
                    )
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            new_gens.append(gen)
        return ChatResult(generations=new_gens)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def get_fake_llm(purpose: str = "matching") -> ToolCapableFakeLLM:
    responses = _DEFAULT_RESPONSES.get(purpose, ["fake response"])
    return ToolCapableFakeLLM(responses=responses)
