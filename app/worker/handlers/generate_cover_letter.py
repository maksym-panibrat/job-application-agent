"""generate-cover-letter handler with replay short-circuit."""

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlmodel import select

from app.config import get_settings
from app.contracts.workflow import GenerationStatus, JobType
from app.database import get_session_factory
from app.models.application import Application
from app.models.work_queue import WorkQueue
from app.services.application_workflow import mark_generation_ready
from app.services.generated_document_service import upsert_generated_document
from app.worker.handlers import HANDLERS, TransientError
from app.worker.payloads import GenerateCoverLetterPayload

log = structlog.get_logger()


class GenerateCoverLetterHandler:
    max_attempts = 5

    async def on_terminal_failure(self, session_factory, row: WorkQueue, error: str) -> None:
        payload = GenerateCoverLetterPayload(**row.payload)
        async with session_factory() as session:
            await session.execute(
                update(Application)
                .where(
                    Application.id == payload.application_id,
                    Application.generation_status.in_(
                        [GenerationStatus.PENDING, GenerationStatus.GENERATING]
                    ),
                )
                .values(generation_status=GenerationStatus.FAILED, updated_at=func.now())
            )
            await session.commit()
        await log.awarning(
            "worker.generate_cover_letter.terminal_failure",
            application_id=str(payload.application_id),
            error=error,
        )

    async def __call__(self, session: AsyncSession, row: WorkQueue) -> None:
        payload = GenerateCoverLetterPayload(**row.payload)
        app = (
            await session.execute(
                select(Application).where(Application.id == payload.application_id)
            )
        ).scalar_one_or_none()
        if app is None:
            await log.awarning(
                "worker.generate_cover_letter.application_missing",
                application_id=str(payload.application_id),
            )
            return
        if app.generation_status == GenerationStatus.READY and app.cover_letter_content is not None:
            await log.ainfo(
                "worker.generate_cover_letter.skip_replay",
                application_id=str(app.id),
            )
            return

        async with get_session_factory()() as claim_session:
            result = await claim_session.execute(
                update(Application)
                .where(
                    Application.id == payload.application_id,
                    Application.generation_status.in_(
                        [GenerationStatus.PENDING, GenerationStatus.GENERATING]
                    ),
                )
                .values(generation_status=GenerationStatus.GENERATING, updated_at=func.now())
            )
            if result.rowcount == 0:
                await log.awarning(
                    "worker.generate_cover_letter.claim_skipped",
                    application_id=str(payload.application_id),
                )
                return
            await claim_session.commit()

        await session.refresh(app)
        try:
            from app.services import application_service

            content = await application_service.generate_materials_llm(
                application=app,
                session=session,
            )
        except Exception as exc:
            from httpx import HTTPStatusError

            if isinstance(exc, HTTPStatusError) and exc.response.status_code == 429:
                retry_after_header = exc.response.headers.get("Retry-After")
                retry_after = (
                    int(retry_after_header)
                    if retry_after_header and retry_after_header.isdigit()
                    else None
                )
                async with get_session_factory()() as reset_session:
                    await reset_session.execute(
                        update(Application)
                        .where(
                            Application.id == payload.application_id,
                            Application.generation_status == GenerationStatus.GENERATING,
                        )
                        .values(generation_status=GenerationStatus.PENDING, updated_at=func.now())
                    )
                    await reset_session.commit()
                raise TransientError(str(exc), retry_after_seconds=retry_after) from exc

            async with get_session_factory()() as fail_session:
                await fail_session.execute(
                    update(Application)
                    .where(Application.id == payload.application_id)
                    .values(generation_status=GenerationStatus.FAILED, updated_at=func.now())
                )
                await fail_session.commit()
            raise

        mark_generation_ready(app, content=content)
        session.add(app)
        await upsert_generated_document(
            session,
            application_id=app.id,
            doc_type="cover_letter",
            content_md=content,
            generation_model=get_settings().llm_generation_model,
            structured_content=None,
        )

        await log.ainfo(
            "worker.generate_cover_letter.done",
            application_id=str(app.id),
        )


HANDLERS[JobType.GENERATE_COVER_LETTER] = GenerateCoverLetterHandler()
