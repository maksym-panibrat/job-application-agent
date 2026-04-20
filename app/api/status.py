from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.database import get_db

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status(session: AsyncSession = Depends(get_db)):
    from app.models.llm_status import LLMStatus

    result = await session.execute(select(LLMStatus).where(LLMStatus.id == 1))
    status = result.scalar_one_or_none()
    now = datetime.now(tz=UTC)
    if status and status.exhausted_until and status.exhausted_until > now:
        return {"budget_exhausted": True, "resumes_at": status.exhausted_until.isoformat()}
    return {"budget_exhausted": False, "resumes_at": None}
