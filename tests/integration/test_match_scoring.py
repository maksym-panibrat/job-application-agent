"""Integration coverage for match application listing."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.match_service import list_applications


async def _seed_profile(db_session) -> UserProfile:
    user = User(id=uuid.uuid4(), email=f"test-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        full_name="Test User",
        email="test@test.com",
        base_resume_md="# Test User\n\nSoftware engineer with 5 years experience.",
        target_roles=["Software Engineer"],
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _seed_job(
    db_session,
    title: str = "Software Engineer",
    salary: str | None = None,
    posted_at: datetime | None = None,
    source: str = "greenhouse",
    company_name: str = "Acme Corp",
) -> Job:
    job = Job(
        source=source,
        external_id=str(uuid.uuid4()),
        title=title,
        company_name=company_name,
        apply_url="https://example.com/apply",
        description="A great engineering role.",
        location="Remote",
        workplace_type="remote",
        salary=salary,
        posted_at=posted_at,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_list_applications_excludes_auto_rejected(db_session):
    profile = await _seed_profile(db_session)
    job1 = await _seed_job(db_session, title="Good Match")
    job2 = await _seed_job(db_session, title="Poor Match")
    job3 = await _seed_job(db_session, title="Legacy Zombie")

    app1 = Application(
        job_id=job1.id, profile_id=profile.id, match_score=0.85, match_rationale="Great"
    )
    app2 = Application(
        job_id=job2.id,
        profile_id=profile.id,
        match_score=0.4,
        status="auto_rejected",
        match_rationale="Weak",
    )
    app3 = Application(job_id=job3.id, profile_id=profile.id)

    for app in [app1, app2, app3]:
        db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session, status="pending_review")
    ids = [str(row[0]) for row in rows]

    assert str(app1.id) in ids
    assert str(app2.id) not in ids
    assert str(app3.id) not in ids


@pytest.mark.asyncio
async def test_list_applications_ordering(db_session):
    profile = await _seed_profile(db_session)

    now = datetime.now(UTC)
    job_a = await _seed_job(
        db_session,
        "High Score Older Salary",
        salary="$100k",
        posted_at=now - timedelta(days=3),
    )
    job_b = await _seed_job(db_session, "High Score Recent No Salary", salary=None, posted_at=now)
    job_c = await _seed_job(
        db_session,
        "Lower Score Newest Salary",
        salary="$90k",
        posted_at=now + timedelta(days=1),
    )
    job_d = await _seed_job(db_session, "High Score Recent Salary", salary="$120k", posted_at=now)

    apps = [
        (job_a, 0.9, now),
        (job_b, 0.9, now + timedelta(seconds=2)),
        (job_c, 0.8, now + timedelta(seconds=3)),
        (job_d, 0.9, now - timedelta(seconds=1)),
    ]
    for job, score, created_at in apps:
        db_session.add(
            Application(
                job_id=job.id,
                profile_id=profile.id,
                match_score=score,
                match_rationale="Good",
                created_at=created_at,
            )
        )
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    titles = [row[10] for row in rows]

    assert titles == [
        "High Score Recent Salary",
        "High Score Recent No Salary",
        "High Score Older Salary",
        "Lower Score Newest Salary",
    ]


@pytest.mark.asyncio
async def test_list_applications_returns_projected_job_data(db_session):
    profile = await _seed_profile(db_session)
    job = await _seed_job(db_session, title="Python Engineer", salary="$120k")

    app = Application(
        job_id=job.id, profile_id=profile.id, match_score=0.8, match_rationale="Great fit"
    )
    db_session.add(app)
    await db_session.commit()

    rows = await list_applications(profile.id, db_session)
    assert len(rows) == 1

    row = rows[0]
    assert row[0] == app.id
    assert row[9] == job.id
    assert row[10] == "Python Engineer"
    assert row[14] == "$120k"
