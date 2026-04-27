"""
Dev-only test seed endpoints.

Only registered in development environment. Used by Playwright E2E tests
to pre-populate the database with known test data.
"""

import uuid
from datetime import UTC, datetime

import jwt as pyjwt
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile
from app.config import get_settings
from app.database import get_db
from app.models.application import Application, GeneratedDocument
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile

router = APIRouter(prefix="/api/test", tags=["test"])

E2E_TEST_USER_ID = uuid.UUID("a0e2e0e2-e0e2-4e2e-9e2e-e2ee2ee2ee2e")

SEED_JOB_1_EXTERNAL_ID = "e2e-seed-job-001"
SEED_JOB_2_EXTERNAL_ID = "e2e-seed-job-002"


@router.post("/login")
async def login_as_test_user(session: AsyncSession = Depends(get_db)) -> dict:
    """
    Create-or-fetch the deterministic e2e test user and return a JWT bearer token.

    Used by Playwright tests to bypass real OAuth — tests stuff the returned
    token into sessionStorage via page.addInitScript. Only mounted when
    ENVIRONMENT is development or test.
    """
    user = await session.get(User, E2E_TEST_USER_ID)
    if user is None:
        user = User(
            id=E2E_TEST_USER_ID,
            email="e2e-test@local",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            hashed_password="",
        )
        session.add(user)
        profile = UserProfile(user_id=E2E_TEST_USER_ID, email=user.email)
        session.add(profile)
        await session.commit()
        await session.refresh(user)

    settings = get_settings()
    token = pyjwt.encode(
        {"sub": str(user.id), "aud": ["fastapi-users:auth"]},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {"access_token": token, "user_id": str(user.id), "email": user.email}


@router.post("/seed")
async def seed_test_data(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Seed the database with deterministic E2E test data.
    Idempotent — safe to call multiple times.
    Returns the IDs of seeded objects.
    """

    # --- Jobs ---
    async def upsert_job(external_id: str, title: str, company: str) -> Job:
        result = await session.execute(
            select(Job).where(Job.source == "e2e", Job.external_id == external_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            job = Job(
                source="e2e",
                external_id=external_id,
                title=title,
                company_name=company,
                location="San Francisco, CA",
                workplace_type="hybrid",
                description_md=f"# {title}\n\nThis is a test job posting for E2E testing.",
                apply_url=f"https://boards.greenhouse.io/testcompany/jobs/{external_id}",
                is_active=True,
                posted_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
        return job

    job1 = await upsert_job(SEED_JOB_1_EXTERNAL_ID, "Senior Software Engineer", "Acme Corp")
    job2 = await upsert_job(SEED_JOB_2_EXTERNAL_ID, "Staff Backend Engineer", "Beta Inc")

    # --- Application with generated documents ---
    async def upsert_application(job: Job) -> Application:
        result = await session.execute(
            select(Application).where(
                Application.job_id == job.id,
                Application.profile_id == profile.id,
            )
        )
        app = result.scalar_one_or_none()
        if app is None:
            app = Application(
                job_id=job.id,
                profile_id=profile.id,
                status="pending_review",
                generation_status="ready",
                match_score=0.87,
                match_rationale="Strong Python and backend experience aligns well.",
                match_strengths=["Python expertise", "FastAPI", "PostgreSQL"],
                match_gaps=[],
            )
            session.add(app)
            await session.commit()
            await session.refresh(app)
        return app

    app1 = await upsert_application(job1)
    app2 = await upsert_application(job2)

    # --- Generated documents for app1 ---
    async def upsert_document(application_id: uuid.UUID, doc_type: str, content: str) -> None:
        result = await session.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.application_id == application_id,
                GeneratedDocument.doc_type == doc_type,
            )
        )
        doc = result.scalar_one_or_none()
        if doc is None:
            doc = GeneratedDocument(
                application_id=application_id,
                doc_type=doc_type,
                content_md=content,
                generation_model="e2e-seed",
            )
            session.add(doc)
            await session.commit()

    await upsert_document(
        app1.id,
        "cover_letter",
        "Dear Hiring Manager,\n\nI am excited to apply for this Senior Software Engineer role.",
    )

    return {
        "jobs": [str(job1.id), str(job2.id)],
        "applications": [str(app1.id), str(app2.id)],
    }


@router.delete("/applications")
async def clear_all_applications(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Delete all applications (and their documents) for the current profile.
    Used by smoke tests to reset scoring state between runs.
    """

    apps_result = await session.execute(
        select(Application).where(Application.profile_id == profile.id)
    )
    apps = list(apps_result.scalars().all())
    app_ids = [a.id for a in apps]

    if app_ids:
        docs_result = await session.execute(
            select(GeneratedDocument).where(GeneratedDocument.application_id.in_(app_ids))
        )
        for doc in docs_result.scalars().all():
            await session.delete(doc)
        await session.flush()

    for app in apps:
        await session.delete(app)

    await session.commit()
    return {"cleared": len(apps)}


@router.post("/generate")
async def run_generation(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Generate cover letters for all pending_review applications without one yet.

    Synchronous: each application's generation runs in this request before
    returning. Smoke tests can immediately read GET /api/applications/{id}
    for generation_status=ready.
    """
    from app.services.application_service import generate_materials

    result = await session.execute(
        select(Application).where(
            Application.profile_id == profile.id,
            Application.status == "pending_review",
            Application.generation_status.in_(("none", "failed")),
        )
    )
    apps = list(result.scalars().all())
    for app in apps:
        try:
            await generate_materials(app.id, session)
        except Exception:
            # generate_materials sets status=failed itself; keep going.
            continue
    return {"triggered": len(apps)}


@router.delete("/seed")
async def clear_seed_data(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Remove all E2E seed data for the current profile."""

    # Find seeded jobs
    jobs_result = await session.execute(select(Job).where(Job.source == "e2e"))
    jobs = list(jobs_result.scalars().all())
    job_ids = [j.id for j in jobs]

    if job_ids:
        apps_result = await session.execute(
            select(Application).where(
                Application.job_id.in_(job_ids),
                Application.profile_id == profile.id,
            )
        )
        apps = list(apps_result.scalars().all())
        app_ids = [a.id for a in apps]

        # Delete documents first (FK: documents -> applications)
        if app_ids:
            docs_result = await session.execute(
                select(GeneratedDocument).where(GeneratedDocument.application_id.in_(app_ids))
            )
            for doc in docs_result.scalars().all():
                await session.delete(doc)
            await session.flush()

        # Then applications (FK: applications -> jobs)
        for app in apps:
            await session.delete(app)
        await session.flush()

    # Then jobs
    for job in jobs:
        await session.delete(job)

    await session.commit()
    return {"cleared": len(jobs)}
