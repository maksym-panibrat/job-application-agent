"""
Integration tests for generate_materials() with a mocked LLM.

Uses FakeListChatModel (injected via ENVIRONMENT=test in get_llm()) to avoid
real API calls while exercising the full DB read/write path through the
generation pipeline.
"""

import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
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
            "doc_type": "tailored_resume",
            "content_md": "# Tailored Resume\n\nContent here.",
            "generation_model": "claude-sonnet-4-6",
        },
        {
            "doc_type": "cover_letter",
            "content_md": "Dear Hiring Manager...",
            "generation_model": "claude-sonnet-4-6",
        },
    ]

    saved = await save_documents(str(application.id), docs, db_session)
    assert len(saved) == 2

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == application.id)
    )
    stored = result.scalars().all()
    assert len(stored) == 2
    types = {d.doc_type for d in stored}
    assert types == {"tailored_resume", "cover_letter"}


@pytest.mark.asyncio
async def test_save_documents_upserts_on_retry(db_session):
    """Calling save_documents twice for the same doc_type updates in place."""
    _, _, _, application = await _seed_db(db_session)

    docs_v1 = [{"doc_type": "tailored_resume", "content_md": "v1", "generation_model": None}]
    docs_v2 = [{"doc_type": "tailored_resume", "content_md": "v2", "generation_model": None}]

    await save_documents(str(application.id), docs_v1, db_session)
    await save_documents(str(application.id), docs_v2, db_session)

    result = await db_session.execute(
        select(GeneratedDocument).where(
            GeneratedDocument.application_id == application.id,
            GeneratedDocument.doc_type == "tailored_resume",
        )
    )
    docs = result.scalars().all()
    assert len(docs) == 1  # no duplicate
    assert docs[0].content_md == "v2"


@pytest.mark.asyncio
async def test_generate_materials_graph_path_with_fake_llm(db_session):
    """
    generate_materials() exercises the LangGraph path (the only path since
    PR 9a removed _generate_direct). ENVIRONMENT=test activates the
    FakeListChatModel shim in get_llm(), so no real API calls are made.
    """
    _, profile, _, application = await _seed_db(db_session)

    checkpointer = MemorySaver()
    await generate_materials(application.id, db_session, checkpointer=checkpointer)

    await db_session.refresh(application)
    assert application.generation_status == "ready"

    result = await db_session.execute(
        select(GeneratedDocument).where(GeneratedDocument.application_id == application.id)
    )
    docs = result.scalars().all()
    assert len(docs) == 2
    doc_types = {d.doc_type for d in docs}
    assert "tailored_resume" in doc_types
    assert "cover_letter" in doc_types


@pytest.mark.asyncio
async def test_generate_materials_max_attempts_guard(db_session):
    """generate_materials() exits early if generation_attempts >= 3."""
    _, _, _, application = await _seed_db(db_session)
    application.generation_attempts = 3
    db_session.add(application)
    await db_session.commit()

    # Should return without setting status to "ready"
    checkpointer = MemorySaver()
    await generate_materials(application.id, db_session, checkpointer=checkpointer)

    await db_session.refresh(application)
    # Status unchanged (still "none" — model default since generation never started)
    assert application.generation_status == "none"
