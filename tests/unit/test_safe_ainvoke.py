from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.llm_safe import BudgetExhausted, safe_ainvoke


@pytest.mark.asyncio
async def test_safe_ainvoke_forwards_kwargs():
    """kwargs (e.g. config=) must reach model.ainvoke."""
    model = MagicMock()
    response = MagicMock()
    model.ainvoke = AsyncMock(return_value=response)

    result = await safe_ainvoke(model, ["msg"], config={"run_name": "test"})

    model.ainvoke.assert_called_once_with(["msg"], config={"run_name": "test"})
    assert result is response


@pytest.mark.asyncio
async def test_safe_ainvoke_raises_budget_exhausted_on_resource_exhausted():
    """ResourceExhausted from Google API → BudgetExhausted."""
    from google.api_core.exceptions import ResourceExhausted

    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=ResourceExhausted("quota exceeded"))

    with pytest.raises(BudgetExhausted):
        await safe_ainvoke(model, ["msg"])


@pytest.mark.asyncio
async def test_safe_ainvoke_raises_budget_exhausted_on_429_quota_string():
    """LangChain-wrapped 429 + quota strings also trigger BudgetExhausted."""
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=Exception("HTTP Error 429: quota exceeded"))

    with pytest.raises(BudgetExhausted):
        await safe_ainvoke(model, ["msg"])


@pytest.mark.asyncio
async def test_safe_ainvoke_propagates_non_quota_exceptions():
    """Non-quota exceptions propagate unchanged."""
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=ValueError("bad input"))

    with pytest.raises(ValueError, match="bad input"):
        await safe_ainvoke(model, ["msg"])


@pytest.mark.asyncio
async def test_safe_ainvoke_writes_db_marker_when_session_provided():
    """When session is passed, the exhausted marker is written to DB."""
    from google.api_core.exceptions import ResourceExhausted

    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=ResourceExhausted("quota exceeded"))
    session = MagicMock()

    with patch("app.agents.llm_safe._write_exhausted_marker") as mock_write:

        async def _noop(*a, **kw):
            return None

        mock_write.side_effect = _noop

        with pytest.raises(BudgetExhausted):
            await safe_ainvoke(model, ["msg"], session=session)

        call_args = mock_write.call_args
        assert call_args is not None
        assert call_args.args[0] is session
