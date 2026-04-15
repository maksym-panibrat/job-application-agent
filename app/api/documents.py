"""Documents endpoints — PDF export."""

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.application import Application, GeneratedDocument
from app.models.user_profile import UserProfile
from app.services import document_service

log = structlog.get_logger()
router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("/{doc_id}/pdf")
async def download_pdf(
    doc_id: str,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    doc = await session.get(GeneratedDocument, uuid.UUID(doc_id))
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify ownership via application → profile chain
    app = await session.get(Application, doc.application_id)
    if not app or app.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_bytes = await document_service.export_pdf(uuid.UUID(doc_id), session)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={doc.doc_type}.pdf"},
    )
