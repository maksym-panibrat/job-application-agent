"""
Integration tests for application_service state transitions and document persistence.
"""

import uuid

import pytest
from sqlmodel import select

from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.application_service import generate_materials, save_documents


async def _seed_application(db_session) -> tuple[Application, Job, UserProfile]:
    """Create User → UserProfile → Job → Application and return all three."""
    user = User(id=uuid.uuid4(), email=f"lifecycle-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    job = Job(
        source="adzuna",
        external_id=str(uuid.uuid4()),
        title="Python Engineer",
        company_name="Acme Corp",
        apply_url="https://example.com/apply",
        description_md="Python backend role.",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app_row = Application(job_id=job.id, profile_id=profile.id)
    db_session.add(app_row)
    await db_session.commit()
    await db_session.refresh(app_row)

    return app_row, job, profile


@pytest.mark.asyncio
async def test_save_documents_creates_row(db_session):
    """save_documents() inserts a GeneratedDocument row."""
    app_row, _, _ = await _seed_application(db_session)
    docs = [{"doc_type": "tailored_resume", "content_md": "# Resume", "generation_model": "test"}]

    await save_documents(str(app_row.id), docs, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].doc_type == "tailored_resume"
    assert rows[0].content_md == "# Resume"


@pytest.mark.asyncio
async def test_save_documents_upserts_on_retry(db_session):
    """Calling save_documents() twice with the same doc_type updates, not duplicates."""
    app_row, _, _ = await _seed_application(db_session)
    docs = [{"doc_type": "tailored_resume", "content_md": "# Draft 1", "generation_model": "test"}]
    await save_documents(str(app_row.id), docs, db_session)

    docs2 = [
        {"doc_type": "tailored_resume", "content_md": "# Final Resume", "generation_model": "test"}
    ]
    await save_documents(str(app_row.id), docs2, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1, "Upsert should produce exactly one row"
    assert rows[0].content_md == "# Final Resume"


@pytest.mark.asyncio
async def test_generate_materials_not_found_no_error(db_session):
    """generate_materials() with a nonexistent UUID returns silently."""
    await generate_materials(uuid.uuid4(), db_session, checkpointer=None)
    # No exception = pass


@pytest.mark.asyncio
async def test_generate_materials_max_attempts_skipped(db_session):
    """Application with generation_attempts=3 is skipped without changing status."""
    app_row, _, _ = await _seed_application(db_session)
    app_row.generation_attempts = 3
    db_session.add(app_row)
    await db_session.commit()

    await generate_materials(app_row.id, db_session, checkpointer=None)

    await db_session.refresh(app_row)
    # status should still be the default "none" (not "generating" or "ready")
    assert app_row.generation_status == "none"
    assert app_row.generation_attempts == 3


@pytest.mark.asyncio
async def test_generate_materials_sets_status_to_ready(db_session):
    """
    generate_materials() with checkpointer=None uses _generate_direct.
    Since ENVIRONMENT=test, get_fake_llm("generation") is used — no real LLM call.
    After completion: generation_status="ready" and GeneratedDocument rows exist.
    """
    app_row, _, _ = await _seed_application(db_session)

    await generate_materials(app_row.id, db_session, checkpointer=None)

    await db_session.refresh(app_row)
    assert app_row.generation_status == "ready"

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == app_row.id)
    )
    docs = result.scalars().all()
    assert len(docs) >= 2  # at minimum: tailored_resume + cover_letter
    doc_types = {d.doc_type for d in docs}
    assert "tailored_resume" in doc_types
    assert "cover_letter" in doc_types
