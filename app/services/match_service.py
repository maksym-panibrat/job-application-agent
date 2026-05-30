"""Match service helpers for profile formatting and application listing."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.application import Application
from app.models.job import Job
from app.models.user_profile import Skill, UserProfile, WorkExperience
from app.services.remote_policy import evaluate_remote_policy, evaluate_us_location_policy

if TYPE_CHECKING:
    from app.agents.matching_agent import ScoreResult

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



class DeterministicRejectionFields(TypedDict):
    score: float
    summary: str
    rationale: str
    strengths: list[str]
    gaps: list[str]
    policy: str


def deterministic_rejection_score(threshold: float) -> float:
    return max(0.0, min(0.29, threshold - 0.01))


_HARD_MISMATCH_CONTRACT_TERMS = (
    "internship",
    "intern",
    "temporary",
    "temp",
    "part-time",
    "part time",
    "contract",
    "1099",
    "commission-only",
    "commission only",
    "volunteer",
    "unpaid",
)

_JUNIOR_TITLE_RE = re.compile(
    r"\b(intern(ship)?|new grad|new-grad|graduate|campus|entry[- ]level|junior|jr\.?|associate)\b",
    re.IGNORECASE,
)

_ROLE_FAMILY_TERMS: dict[str, tuple[str, ...]] = {
    "engineering": (
        "engineer",
        "developer",
        "software",
        "backend",
        "front end",
        "frontend",
        "full stack",
        "fullstack",
        "platform",
        "infrastructure",
        "sre",
        "devops",
        "data engineer",
        "machine learning",
        "ml engineer",
        "ai engineer",
        "security engineer",
        "architect",
    ),
    "product": ("product manager", "product lead", "product owner"),
    "design": ("designer", "design", "ux", "ui"),
    "data": ("data scientist", "analyst", "analytics", "bi "),
    "sales": (
        "account executive",
        "sales",
        "business development",
        "bdr",
        "sdr",
        "enterprise account",
        "strategic account",
        "revenue",
        "quota",
    ),
    "customer_success": (
        "customer success",
        "solutions consultant",
        "implementation consultant",
        "support specialist",
    ),
    "recruiting": ("recruiter", "talent acquisition", "sourcer"),
    "marketing": ("marketing", "growth manager", "content", "brand"),
    "finance": ("finance", "accounting", "controller", "fp&a"),
    "legal": ("counsel", "legal", "attorney", "paralegal"),
    "operations": ("operations", "chief of staff", "program manager"),
}

_NON_TECH_ROLE_FAMILIES = {
    "sales",
    "customer_success",
    "recruiting",
    "marketing",
    "finance",
    "legal",
}

_ENGINEERING_SKILL_TERMS = (
    "python",
    "typescript",
    "javascript",
    "java",
    "go",
    "rust",
    "postgres",
    "kubernetes",
    "docker",
    "api",
    "distributed",
    "platform",
    "backend",
    "infrastructure",
    "aws",
    "gcp",
    "azure",
)


def _normalized_text(*parts: str | None) -> str:
    return " ".join(part or "" for part in parts).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _contract_type_rejection(
    profile: UserProfile,
    job: Job,
    threshold: float,
) -> DeterministicRejectionFields | None:
    _ = profile
    text = _normalized_text(job.contract_type, job.title, job.description or job.description_raw)
    term = next((term for term in _HARD_MISMATCH_CONTRACT_TERMS if term in text), None)
    if term is None:
        return None
    gap = f"Non-target employment type: {term}"
    return {
        "score": deterministic_rejection_score(threshold),
        "summary": "Deterministic mismatch: employment type",
        "rationale": gap,
        "strengths": [],
        "gaps": [gap],
        "policy": "contract_type",
    }


def _seniority_rejection(
    profile: UserProfile,
    job: Job,
    threshold: float,
) -> DeterministicRejectionFields | None:
    profile_seniority = (profile.seniority or "").lower()
    if not any(
        seniority in profile_seniority
        for seniority in ("senior", "staff", "principal")
    ):
        return None
    match = _JUNIOR_TITLE_RE.search(job.title or "")
    if match is None:
        return None
    gap = f"Role seniority is {match.group(0).lower()}, below target seniority"
    return {
        "score": deterministic_rejection_score(threshold),
        "summary": "Deterministic mismatch: seniority",
        "rationale": gap,
        "strengths": [],
        "gaps": [gap],
        "policy": "seniority",
    }


def role_families_for_text(text: str) -> set[str]:
    lowered = text.lower()
    return {
        family
        for family, terms in _ROLE_FAMILY_TERMS.items()
        if _contains_any(lowered, terms)
    }


def _profile_role_families(profile: UserProfile) -> set[str]:
    return role_families_for_text(" ".join(profile.target_roles or []))


def _role_family_rejection(
    profile: UserProfile,
    job: Job,
    threshold: float,
) -> DeterministicRejectionFields | None:
    target_families = _profile_role_families(profile)
    if not target_families:
        return None
    job_families = role_families_for_text(job.title or "")
    if not job_families:
        return None
    if "engineering" in target_families and job_families <= _NON_TECH_ROLE_FAMILIES:
        gap = "Job title is outside target role families"
        return {
            "score": deterministic_rejection_score(threshold),
            "summary": "Deterministic mismatch: role family",
            "rationale": gap,
            "strengths": [],
            "gaps": [gap],
            "policy": "role_family",
        }
    return None


def candidate_priority_score(profile: UserProfile, job: Job) -> float:
    """Cheap ordering score for deciding which uncertain jobs deserve LLM spend first."""
    score = 0.0
    target_families = _profile_role_families(profile)
    job_families = role_families_for_text(job.title or "")
    if target_families and job_families:
        score += 4.0 if target_families & job_families else -2.0

    profile_tokens = set(
        re.findall(r"[a-z0-9+#.]{3,}", " ".join(profile.target_roles or []).lower())
    )
    title_tokens = set(re.findall(r"[a-z0-9+#.]{3,}", (job.title or "").lower()))
    score += min(2.0, len(profile_tokens & title_tokens) * 0.5)

    if (job.workplace_type or "").lower() == "remote" and profile.remote_ok:
        score += 1.0
    location_text = (job.location or "").lower()
    if any((loc or "").lower() in location_text for loc in profile.target_locations or []):
        score += 1.0

    description_text = (job.description or job.description_raw or "").lower()
    skill_hits = sum(1 for term in _ENGINEERING_SKILL_TERMS if term in description_text)
    score += min(2.0, skill_hits * 0.25)
    if job.contract_type and "full" in job.contract_type.lower():
        score += 0.5
    return score


def deterministic_rejection_fields(
    profile: UserProfile,
    job: Job,
    threshold: float,
) -> DeterministicRejectionFields | None:
    us_verdict = evaluate_us_location_policy(job)
    if us_verdict.hard_mismatch:
        gap = us_verdict.gap or "Deterministic match policy mismatch"
        return {
            "score": deterministic_rejection_score(threshold),
            "summary": "Deterministic mismatch: non-US position",
            "rationale": gap,
            "strengths": [],
            "gaps": [gap],
            "policy": "us_location",
        }

    remote_verdict = evaluate_remote_policy(profile, job)
    if remote_verdict.hard_mismatch:
        gap = remote_verdict.gap or "Deterministic match policy mismatch"
        return {
            "score": deterministic_rejection_score(threshold),
            "summary": "Deterministic mismatch: recurring office attendance requirement",
            "rationale": gap,
            "strengths": [],
            "gaps": [gap],
            "policy": "remote_office",
        }

    contract_verdict = _contract_type_rejection(profile, job, threshold)
    if contract_verdict is not None:
        return contract_verdict

    seniority_verdict = _seniority_rejection(profile, job, threshold)
    if seniority_verdict is not None:
        return seniority_verdict

    role_family_verdict = _role_family_rejection(profile, job, threshold)
    if role_family_verdict is not None:
        return role_family_verdict

    return None


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
