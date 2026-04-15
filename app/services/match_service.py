"""
Match service — scores jobs against a profile and creates Application rows.
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import Skill, UserProfile, WorkExperience
from app.services import profile_service

log = structlog.get_logger()


def format_profile_text(
    profile: UserProfile,
    skills: list[Skill],
    experiences: list[WorkExperience],
) -> str:
    """Render profile as markdown text for LLM consumption."""
    lines = []
    if profile.full_name:
        lines.append(f"# {profile.full_name}")
    if profile.seniority:
        lines.append(f"Seniority: {profile.seniority}")
    if profile.target_roles:
        lines.append(f"Target roles: {', '.join(profile.target_roles)}")
    if profile.remote_ok:
        lines.append("Open to remote: yes")

    if skills:
        lines.append("\n## Skills")
        by_category: dict[str, list[str]] = {}
        for s in skills:
            cat = s.category or "other"
            by_category.setdefault(cat, []).append(s.name)
        for cat, names in by_category.items():
            lines.append(f"- {cat}: {', '.join(names)}")

    if experiences:
        lines.append("\n## Work Experience")
        for exp in experiences:
            end = exp.end_date.year if exp.end_date else "present"
            lines.append(f"### {exp.title} at {exp.company} ({exp.start_date.year}–{end})")
            if exp.description_md:
                lines.append(exp.description_md[:500])

    if profile.base_resume_md:
        lines.append("\n## Resume")
        lines.append(profile.base_resume_md[:3000])

    return "\n".join(lines)


async def get_or_create_application(
    job_id: uuid.UUID,
    profile_id: uuid.UUID,
    session: AsyncSession,
) -> Application | None:
    """Create an Application row. Returns None if already exists (idempotent)."""
    result = await session.execute(
        select(Application).where(
            Application.job_id == job_id,
            Application.profile_id == profile_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return None

    app = Application(job_id=job_id, profile_id=profile_id)
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return app


async def score_and_match(
    profile: UserProfile,
    session: AsyncSession,
    jobs: list[Job] | None = None,
) -> list[Application]:
    """
    Score all unmatched jobs for a profile and create Application rows above threshold.
    Uses the LangGraph matching agent with Send-based fan-out for parallelism.
    """
    settings = get_settings()

    skills = await profile_service.get_skills(profile.id, session)
    experiences = await profile_service.get_work_experiences(profile.id, session)
    profile_text = format_profile_text(profile, skills, experiences)

    if jobs is None:
        # Fetch active jobs not already matched for this profile
        matched_result = await session.execute(
            select(Application.job_id).where(Application.profile_id == profile.id)
        )
        matched_ids = {row[0] for row in matched_result.all()}

        all_jobs_result = await session.execute(
            select(Job).where(Job.is_active.is_(True)).limit(100)
        )
        jobs = [j for j in all_jobs_result.scalars().all() if j.id not in matched_ids]

    if not jobs:
        return []

    from app.agents.matching_agent import JobContext, build_graph

    job_contexts: list[JobContext] = []
    job_map: dict[str, Job] = {}
    for job in jobs:
        # We need application rows first so we can link scores
        app = await get_or_create_application(job.id, profile.id, session)
        if app is None:
            # Already exists
            result = await session.execute(
                select(Application).where(
                    Application.job_id == job.id,
                    Application.profile_id == profile.id,
                )
            )
            app = result.scalar_one_or_none()
        if app:
            job_contexts.append(
                {
                    "application_id": str(app.id),
                    "title": job.title,
                    "company": job.company_name,
                    "description": job.description_md or "",
                }
            )
            job_map[str(app.id)] = job

    if not job_contexts:
        return []

    graph = build_graph()
    result = await graph.ainvoke(
        {
            "profile_id": str(profile.id),
            "profile_text": profile_text,
            "jobs": job_contexts,
            "scores": [],
        }
    )

    scored_apps = []
    for score_result in result.get("scores", []):
        if score_result.score < settings.match_score_threshold:
            continue

        # Update application with score
        app_result = await session.execute(
            select(Application).where(
                Application.id == uuid.UUID(score_result.application_id)
            )
        )
        app = app_result.scalar_one_or_none()
        if app:
            app.match_score = score_result.score
            app.match_rationale = score_result.rationale
            app.match_strengths = score_result.strengths
            app.match_gaps = score_result.gaps
            session.add(app)
            scored_apps.append(app)

    await session.commit()
    await log.ainfo(
        "match.complete",
        profile_id=str(profile.id),
        scored=len(scored_apps),
        total_jobs=len(job_contexts),
    )
    return scored_apps


async def list_applications(
    profile_id: uuid.UUID,
    session: AsyncSession,
    status: str | None = None,
    min_score: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Application]:
    q = select(Application).where(Application.profile_id == profile_id)
    if status:
        q = q.where(Application.status == status)
    if min_score is not None:
        q = q.where(Application.match_score >= min_score)
    q = q.order_by(Application.match_score.desc().nullslast(), Application.created_at.desc())
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())
