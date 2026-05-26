"""
Match service — scores jobs against a profile and creates Application rows.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import Skill, UserProfile, WorkExperience
from app.services import profile_service
from app.services.remote_policy import evaluate_remote_policy

if TYPE_CHECKING:
    from app.agents.matching_agent import ScoreResult

log = structlog.get_logger()
DISPLAY_JOB_MAX_AGE_DAYS = 10

ApplicationListRow = tuple[
    uuid.UUID,
    str,
    str,
    float | None,
    str | None,
    str | None,
    list[str],
    list[str],
    datetime,
    uuid.UUID,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    datetime | None,
]

JobScoreRow = tuple[
    uuid.UUID,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]


async def mark_for_rescore(profile_id: uuid.UUID, session: AsyncSession) -> int:
    """Clear eligible scored applications and enqueue match work for re-scoring.

    Eligibility: status IN ('pending_review', 'auto_rejected') AND match_score IS NOT NULL.
    Leaves dismissed/applied user decisions untouched.
    """
    from app.worker.queue_service import enqueue

    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Application.id).where(
                Application.profile_id == profile_id,
                col(Application.status).in_(("pending_review", "auto_rejected")),
                col(Application.match_score).is_not(None),
            )
        )
    ).all()
    app_ids = [row[0] for row in rows]
    if not app_ids:
        return 0

    await session.execute(
        update(Application)
        .where(
            col(Application.id).in_(app_ids),
        )
        .values(
            match_score=None,
            match_summary=None,
            match_rationale=None,
            match_strengths=[],
            match_gaps=[],
            status="pending_review",
            updated_at=now,
        )
    )
    for app_id in app_ids:
        await enqueue(
            session,
            job_type="match",
            payload={"application_id": str(app_id)},
            dedupe_key=f"match:{app_id}",
        )
    await session.commit()
    return len(app_ids)


def format_profile_text(
    profile: UserProfile,
    skills: list[Skill],
    experiences: list[WorkExperience],
) -> str:
    """Render profile as markdown text for LLM consumption.

    Always emits a 'Locations:' line so the matching LLM never has to
    infer the candidate's location stance from the absence of a field.
    """
    lines = []
    if profile.full_name:
        lines.append(f"# {profile.full_name}")
    if profile.seniority:
        lines.append(f"Seniority: {profile.seniority}")
    if profile.target_roles:
        lines.append(f"Target roles: {', '.join(profile.target_roles)}")

    locs = list(profile.target_locations or [])
    locs_str = ", ".join(locs) if locs else "(none)"
    remote_str = "yes" if profile.remote_ok else "no"
    lines.append(f"Target locations: {locs_str}")
    lines.append(f"Open to remote: {remote_str}")

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


def apply_remote_policy_to_score(
    score_result: "ScoreResult",
    profile: UserProfile,
    job: Job,
    threshold: float,
) -> "ScoreResult":
    """Cap passing scores when deterministic remote policy finds a hard mismatch."""
    verdict = evaluate_remote_policy(profile, job)
    if not verdict.hard_mismatch or score_result.score is None:
        return score_result

    if score_result.score >= threshold:
        score_result.score = max(0.0, min(0.29, threshold - 0.01))

    if verdict.gap:
        if verdict.gap not in score_result.gaps:
            score_result.gaps.append(verdict.gap)
        if verdict.gap not in score_result.rationale:
            score_result.rationale = f"{score_result.rationale} {verdict.gap}".strip()

    return score_result


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
        company_ids = list(profile.target_company_ids or [])
        if not company_ids:
            return []

        matched_result = await session.execute(
            select(Application.job_id).where(
                Application.profile_id == profile.id,
                col(Application.match_score).is_not(None),
            )
        )
        matched_ids = {row[0] for row in matched_result.all()}

        candidates_q = build_score_candidate_query(
            company_ids=company_ids,
            matched_ids=matched_ids,
            limit=settings.matching_jobs_per_batch,
        )
        candidate_rows: list[JobScoreRow] = list(
            (await session.execute(candidates_q)).tuples().all()
        )
        jobs = [
            Job(
                id=job_id,
                source=source,
                external_id=external_id,
                title=title,
                company_name=company_name,
                location=location,
                workplace_type=workplace_type,
                description=description,
                description_raw=description_raw,
                apply_url=apply_url,
            )
            for (
                job_id,
                source,
                external_id,
                title,
                company_name,
                location,
                workplace_type,
                description,
                description_raw,
                apply_url,
            ) in candidate_rows
        ]

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
                    "location": job.location,
                    "workplace_type": job.workplace_type,
                    "description": job.description or job.description_raw or "",
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
        job = job_map.get(score_result.application_id)
        if job is not None:
            score_result = apply_remote_policy_to_score(
                score_result,
                profile,
                job,
                settings.match_score_threshold,
            )

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
        app.match_summary = score_result.summary
        app.match_rationale = score_result.rationale
        app.match_strengths = score_result.strengths
        app.match_gaps = score_result.gaps

        passed = score_result.score >= settings.match_score_threshold
        if not passed:
            if app.status == "pending_review":
                app.status = "auto_rejected"
        else:
            scored_apps.append(app)

        session.add(app)
        await log.ainfo(
            "match.scored",
            application_id=score_result.application_id,
            score=round(score_result.score, 3),
            passed=passed,
            summary=score_result.summary[:200],
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


async def list_applications(
    profile_id: uuid.UUID,
    session: AsyncSession,
    status: str | None = None,
    min_score: float | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ApplicationListRow]:
    q = build_application_list_query(
        profile_id,
        status=status,
        min_score=min_score,
    )
    q = q.order_by(
        Application.match_score.desc().nullslast(),
        Job.posted_at.desc().nullslast(),
        Job.salary.isnot(None).desc(),
        Application.created_at.desc(),
    )
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.tuples().all())


def build_application_list_query(
    profile_id: uuid.UUID,
    *,
    status: str | None,
    min_score: float | None,
):
    posted_cutoff = datetime.now(UTC) - timedelta(days=DISPLAY_JOB_MAX_AGE_DAYS)
    q = (
        select(
            Application.id,
            Application.status,
            Application.generation_status,
            Application.match_score,
            Application.match_summary,
            Application.match_rationale,
            Application.match_strengths,
            Application.match_gaps,
            Application.created_at,
            Job.id,
            Job.title,
            Job.company_name,
            Job.location,
            Job.workplace_type,
            Job.salary,
            Job.contract_type,
            Job.apply_url,
            Job.posted_at,
        )
        .join(Job, Application.job_id == Job.id)
        .where(Application.profile_id == profile_id)
        .where((col(Job.posted_at).is_(None)) | (Job.posted_at >= posted_cutoff))
    )
    if status:
        q = q.where(Application.status == status)
        if status == "pending_review":
            q = q.where(col(Application.match_score).is_not(None))
    if min_score is not None:
        q = q.where(Application.match_score >= min_score)
    return q


def build_score_candidate_query(
    *,
    company_ids: list[uuid.UUID],
    matched_ids: set[uuid.UUID],
    limit: int,
):
    q = (
        select(
            Job.id,
            Job.source,
            Job.external_id,
            Job.title,
            Job.company_name,
            Job.location,
            Job.workplace_type,
            Job.description,
            Job.description_raw,
            Job.apply_url,
        )
        .where(
            Job.is_active.is_(True),
            col(Job.company_id).in_(company_ids),
        )
        .order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
        .limit(limit)
    )
    if matched_ids:
        q = q.where(col(Job.id).notin_(matched_ids))
    return q
