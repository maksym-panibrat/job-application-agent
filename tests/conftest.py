from unittest.mock import patch

import pytest

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


def pytest_addoption(parser):
    parser.addoption(
        "--catalog-live",
        action="store_true",
        default=False,
        help=(
            "Run the catalog-live validation tests against real public ATS boards. "
            "Used by the nightly validate-catalog GitHub Actions workflow; off by default "
            "for local + PR CI runs."
        ),
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--catalog-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --catalog-live to run")
    for item in items:
        if "catalog_live" in item.keywords:
            item.add_marker(skip_live)
