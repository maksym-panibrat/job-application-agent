"""
Match service — scores jobs against a profile and creates Application rows.
"""

import time
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
    t0 = time.perf_counter()
    await log.ainfo("match.score_and_match.started", profile_id=str(profile.id))
    settings = get_settings()

    skills = await profile_service.get_skills(profile.id, session)
    experiences = await profile_service.get_work_experiences(profile.id, session)
    profile_text = format_profile_text(profile, skills, experiences)

    if jobs is None:
        from app.data.slug_company import slug_to_company_name

        slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
        if not slugs:
            return []
        company_names = [slug_to_company_name(s) for s in slugs]

        matched_result = await session.execute(
            select(Application.job_id).where(
                Application.profile_id == profile.id,
                Application.match_score.isnot(None),
            )
        )
        matched_ids = {row[0] for row in matched_result.all()}

        candidates_q = (
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.source == "greenhouse_board",
                Job.company_name.in_(company_names),
            )
            .order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
        )
        if matched_ids:
            candidates_q = candidates_q.where(Job.id.notin_(matched_ids))
        candidates_q = candidates_q.limit(settings.matching_jobs_per_batch)

        all_jobs_result = await session.execute(candidates_q)
        jobs = list(all_jobs_result.scalars().all())

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
        },
        config={
            "run_name": "match-scoring",
            "metadata": {"profile_id": str(profile.id), "job_count": len(job_contexts)},
        },
    )

    scored_apps = []
    for score_result in result.get("scores", []):
        app_result = await session.execute(
            select(Application).where(Application.id == uuid.UUID(score_result.application_id))
        )
        app = app_result.scalar_one_or_none()
        if not app:
            continue

        # score=None means scoring was skipped (rate limit / quota / transient).
        # Leave match_score NULL so the Application is re-eligible on the next
        # sync instead of being permanently auto_rejected at 0.0 (issue #46).
        if score_result.score is None:
            await log.awarning(
                "match.scoring_skipped",
                application_id=score_result.application_id,
                rationale=score_result.rationale[:200],
            )
            continue

        # Always persist scores for auditability
        app.match_score = score_result.score
        app.match_rationale = score_result.rationale
        app.match_strengths = score_result.strengths
        app.match_gaps = score_result.gaps

        passed = score_result.score >= settings.match_score_threshold
        if not passed:
            app.status = "auto_rejected"
        else:
            scored_apps.append(app)

        session.add(app)
        await log.ainfo(
            "match.scored",
            application_id=score_result.application_id,
            score=round(score_result.score, 3),
            passed=passed,
            rationale=score_result.rationale[:200],
        )

    await session.commit()
    await log.ainfo(
        "match.complete",
        profile_id=str(profile.id),
        scored=len(scored_apps),
        total_jobs=len(job_contexts),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
    return scored_apps


async def score_cached(
    profile: UserProfile,
    session: AsyncSession,
    *,
    cap: int | None = None,
) -> list[Application]:
    """Variant of score_and_match that scores at most `cap` already-cached jobs.
    No fetches, no slug-pool growth. Used by the instant-feedback path of POST /api/jobs/sync."""
    from app.config import get_settings

    settings = get_settings()
    cap = cap if cap is not None else settings.matching_jobs_per_batch

    from app.data.slug_company import slug_to_company_name

    slugs = (profile.target_company_slugs or {}).get("greenhouse", []) or []
    if not slugs:
        return []
    company_names = [slug_to_company_name(s) for s in slugs]

    matched_result = await session.execute(
        select(Application.job_id).where(
            Application.profile_id == profile.id,
            Application.match_score.isnot(None),
        )
    )
    matched_ids = {row[0] for row in matched_result.all()}

    q = (
        select(Job)
        .where(
            Job.is_active.is_(True),
            Job.source == "greenhouse_board",
            Job.company_name.in_(company_names),
        )
        .order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
    )
    if matched_ids:
        q = q.where(Job.id.notin_(matched_ids))
    q = q.limit(cap)
    jobs_result = await session.execute(q)
    jobs = list(jobs_result.scalars().all())
    if not jobs:
        return []
    return await score_and_match(profile, session, jobs=jobs)


async def list_applications(
    profile_id: uuid.UUID,
    session: AsyncSession,
    status: str | None = None,
    min_score: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[tuple[Application, Job]]:
    q = (
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .where(Application.profile_id == profile_id)
    )
    if status:
        q = q.where(Application.status == status)
        if status == "pending_review":
            q = q.where(Application.match_score.isnot(None))
    if min_score is not None:
        q = q.where(Application.match_score >= min_score)
    q = q.order_by(
        Application.match_score.desc().nullslast(),
        Job.salary.isnot(None).desc(),
        Job.posted_at.desc().nullslast(),
        Application.created_at.desc(),
    )
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.tuples().all())
