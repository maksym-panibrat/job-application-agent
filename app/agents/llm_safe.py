"""
LLM safety wrapper: catches ResourceExhausted from Gemini, raises BudgetExhausted,
and writes a marker to llm_status so the UI can show a budget-exhausted banner.
"""

from datetime import UTC, datetime

from google.api_core.exceptions import ResourceExhausted as GaxResourceExhausted
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


class BudgetExhausted(Exception):
    """Raised when the Gemini API quota is exhausted for this billing period."""

    def __init__(self, resumes_at: datetime):
        self.resumes_at = resumes_at
        super().__init__(f"LLM budget exhausted until {resumes_at.isoformat()}")


def _next_month_utc() -> datetime:
    now = datetime.now(tz=UTC)
    if now.month == 12:
        return now.replace(
            year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


async def safe_ainvoke(
    model: BaseChatModel,
    messages,
    session=None,
    **kwargs,
) -> BaseMessage:
    """
    Invoke model.ainvoke(messages, **kwargs). If the Gemini quota is exhausted
    (ResourceExhausted or a 429 response containing 'quota'), write llm_status
    marker and raise BudgetExhausted. Other exceptions propagate normally.
    """
    try:
        return await model.ainvoke(messages, **kwargs)
    except Exception as exc:
        exc_str = str(exc)
        is_budget_exhausted = isinstance(exc, GaxResourceExhausted) or (
            "429" in exc_str and "quota" in exc_str.lower()
        )
        if is_budget_exhausted:
            resumes_at = _next_month_utc()
            if session is not None:
                await _write_exhausted_marker(session, resumes_at)
            raise BudgetExhausted(resumes_at) from exc
        raise


async def _write_exhausted_marker(session, resumes_at: datetime) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.llm_status import LLMStatus

    stmt = pg_insert(LLMStatus).values(id=1, exhausted_until=resumes_at)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"exhausted_until": resumes_at},
    )
    await session.execute(stmt)
    await session.commit()
