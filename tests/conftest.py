from unittest.mock import patch

from app.agents.test_llm import ToolCapableFakeLLM


def patch_llm(module_path: str, responses: list[str]):
    """
    Return a unittest.mock.patch context manager that replaces get_llm() at
    `module_path` with ToolCapableFakeLLM(responses=responses).

    Usage:
        with patch_llm("app.agents.onboarding", ["Hello!"]):
            result = await graph.ainvoke(...)
    """
    fake = ToolCapableFakeLLM(responses=responses)
    return patch(f"{module_path}.get_llm", return_value=fake)
