"""Integration tests for generate_materials() with a fake LLM (sync, cover-letter only).

ENVIRONMENT=test activates FakeListChatModel in get_llm(), so no real LLM calls.
"""

import uuid

import pytest
from sqlmodel import select

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.application_service import generate_materials, save_documents


async def _seed_db(db_session):
    """Seed a user, profile, job, and application for testing."""
    user = User(id=uuid.uuid4(), email=f"test-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Jane Doe",
        email="jane@test.com",
        base_resume_md="# Jane Doe\n\n## Experience\n- Backend Engineer at Acme",
        target_roles=["Senior Backend Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title="Senior Python Engineer",
        company_name="Beta Corp",
        apply_url="https://jobs.lever.co/beta/abc-123",
        description_md="We need a Python expert for distributed systems.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    application = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)

    return user, profile, job, application


@pytest.mark.asyncio
async def test_save_documents_creates_records(db_session):
    _, _, _, application = await _seed_db(db_session)

    docs = [
        {
            "doc_type": "cover_letter",
            "content_md": "Dear Hiring Team...",
            "generation_model": "test-model",
        },
    ]

    saved = await save_documents(str(application.id), docs, db_session)
    assert len(saved) == 1

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == application.id)
    )
    stored = result.scalars().all()
    assert len(stored) == 1
    assert stored[0].doc_type == "cover_letter"


@pytest.mark.asyncio
async def test_save_documents_upserts_on_retry(db_session):
    """Calling save_documents twice for the same doc_type updates in place."""
    _, _, _, application = await _seed_db(db_session)

    docs_v1 = [{"doc_type": "cover_letter", "content_md": "v1", "generation_model": None}]
    docs_v2 = [{"doc_type": "cover_letter", "content_md": "v2", "generation_model": None}]

    await save_documents(str(application.id), docs_v1, db_session)
    await save_documents(str(application.id), docs_v2, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.doc_type == "cover_letter",
        )
    )
    docs = result.scalars().all()
    assert len(docs) == 1  # no duplicate
    assert docs[0].content_md == "v2"


@pytest.mark.asyncio
async def test_generate_materials_sync_persists_cover_letter(db_session):
    """generate_materials() runs the cover-letter graph synchronously and persists the doc."""
    _, _, _, application = await _seed_db(db_session)

    doc = await generate_materials(application.id, db_session)

    assert doc.doc_type == "cover_letter"
    assert len(doc.content_md) > 0

    await db_session.refresh(application)
    assert application.generation_status == "ready"

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == application.id)
    )
    docs = result.scalars().all()
    assert len(docs) == 1
    assert docs[0].doc_type == "cover_letter"
