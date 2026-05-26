from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.services.feedback_service import (
    FeedbackValidationError,
    create_feedback_report,
)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackSubmitRequest(BaseModel):
    category: str
    message: str
    diagnostics: Any = None


class FeedbackSubmitResponse(BaseModel):
    id: UUID
    created: bool
    notification_status: str


@router.post("", response_model=FeedbackSubmitResponse)
async def submit_feedback(
    body: FeedbackSubmitRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FeedbackSubmitResponse:
    try:
        result = await create_feedback_report(
            user=user,
            category=body.category,
            message=body.message,
            diagnostics=body.diagnostics,
            session=session,
            settings=settings,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FeedbackSubmitResponse(
        id=result.id,
        created=True,
        notification_status=result.notification_status,
    )
