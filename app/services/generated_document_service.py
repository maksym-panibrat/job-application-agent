"""Persistence helpers for generated application documents."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.application import GeneratedDocument


async def upsert_generated_document(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    doc_type: str,
    content_md: str,
    generation_model: str | None,
    structured_content: dict[str, Any] | None = None,
) -> GeneratedDocument:
    existing = (
        await session.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.application_id == application_id,
                GeneratedDocument.doc_type == doc_type,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = GeneratedDocument(
            application_id=application_id,
            doc_type=doc_type,
            content_md=content_md,
            generation_model=generation_model,
            structured_content=structured_content,
        )
    else:
        existing.content_md = content_md
        existing.generation_model = generation_model
        existing.structured_content = structured_content
    session.add(existing)
    return existing
