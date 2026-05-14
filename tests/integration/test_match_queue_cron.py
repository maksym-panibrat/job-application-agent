"""Integration test for run_match_queue cron worker."""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from app.data.slug_company import slug_to_company_name
from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.models.user_profile import UserProfile
from app.scheduler.tasks import run_match_queue
from app.services import match_queue_service


async def _ensure_company(db_session, slug: str) -> Company:
    existing = (
        await db_session.execute(sa.select(Company).where(Company.normalized_key == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    company = Company(
        canonical_name=slug_to_company_name(slug),
        normalized_key=slug,
        provider_slugs={"greenhouse": slug},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


async def _seed_profile(db_session, *slugs: str) -> UserProfile:
    """Seed a User + UserProfile (FK constraint requires the user row first).

    Also seeds Company rows + UserProfile.target_company_ids — the matching
    pipeline reads the new column post-D6.
    """
    user = User(id=uuid.uuid4(), email=f"mqcron-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()
    company_ids: list[uuid.UUID] = []
    for slug in slugs:
        company = await _ensure_company(db_session, slug)
        company_ids.append(company.id)
    profile = UserProfile(
        user_id=user.id,
        target_company_ids=company_ids,
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


async def _job_for(db_session, slug: str, *, external_id: str, **kwargs) -> Job:
    company = await _ensure_company(db_session, slug)
    return Job(
        source="greenhouse",
        external_id=external_id,
        title=kwargs.pop("title", "Engineer"),
        company_name=slug_to_company_name(slug),
        company_id=company.id,
        apply_url=kwargs.pop("apply_url", f"https://x/{external_id}"),
        is_active=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_run_match_queue_drains_pending(db_session):
    await _seed_profile(db_session, "airbnb")
    job = await _job_for(db_session, "airbnb", external_id="x-1")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)

    # Patch the LangGraph build_graph to return a passing score
    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        return {
            "scores": [
                ScoreResult(
                    application_id=state["jobs"][0]["application_id"],
                    score=0.9,
                    rationale="great fit",
                    strengths=["python"],
                    gaps=[],
                )
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue()

    assert result["attempted"] == 1
    assert result["succeeded"] == 1

    # run_match_queue commits via separate sessions; expire to re-read
    db_session.expire_all()
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    assert len(apps) == 1
    assert apps[0].match_status == "matched"
    assert apps[0].match_score == 0.9


@pytest.mark.asyncio
async def test_run_match_queue_releases_leases_without_failing_attempts_on_budget(db_session):
    """When score_and_match raises BudgetExhausted (Gemini quota gone), the
    match queue must NOT mark every claimed app as failed. That's how a brief
    credit outage silently moves perfectly good pending_match apps to
    match_status='error' (#74). Instead: clear claimed_at to release the lease,
    leave attempts unchanged. Next tick re-claims naturally once budget restores."""
    from datetime import UTC, datetime

    from app.agents.llm_safe import BudgetExhausted

    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id

    jobs = []
    for i in range(3):
        job = await _job_for(
            db_session,
            "airbnb",
            external_id=f"budget-{i}",
            title=f"Engineer {i}",
            apply_url=f"https://x/{i}",
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.commit()
    for j in jobs:
        await db_session.refresh(j)
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    resumes_at = datetime(2026, 6, 1, tzinfo=UTC)

    async def _raise_budget(*args, **kwargs):
        raise BudgetExhausted(resumes_at)

    with patch("app.services.match_service.score_and_match", side_effect=_raise_budget):
        result = await run_match_queue()

    db_session.expire_all()
    apps = (
        (
            await db_session.execute(
                sa.select(Application).where(Application.profile_id == profile_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(apps) == 3
    for app in apps:
        assert app.match_status == "pending_match", (
            f"budget-exhausted apps must NOT flip to error: status={app.match_status}"
        )
        assert app.match_attempts == 0, (
            f"budget exhausted is not the app's fault — attempts must not increment: "
            f"attempts={app.match_attempts}"
        )
        assert app.match_claimed_at is None, "lease must be released so next tick re-claims"

    assert result.get("budget_exhausted") is True, (
        f"result must surface budget_exhausted flag, got {result}"
    )


@pytest.mark.asyncio
async def test_run_match_queue_releases_claim_without_failed_attempt_on_skipped_score(db_session):
    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id
    job = await _job_for(db_session, "airbnb", external_id="skipped-score")
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    await match_queue_service.enqueue_for_interested_profiles(job, db_session)

    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        return {
            "scores": [
                ScoreResult(
                    application_id=state["jobs"][0]["application_id"],
                    score=None,
                    summary="deferred",
                    rationale="LLM scoring temporarily unavailable",
                    strengths=[],
                    gaps=[],
                )
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue()

    assert result["attempted"] == 1
    assert result["succeeded"] == 0
    assert result["failed"] == 0
    assert result["deferred"] == 1

    db_session.expire_all()
    app = (
        await db_session.execute(
            sa.select(Application).where(Application.profile_id == profile_id)
        )
    ).scalar_one()
    assert app.match_status == "pending_match"
    assert app.match_score is None
    assert app.match_attempts == 0
    assert app.match_claimed_at is None


@pytest.mark.asyncio
async def test_run_match_queue_uses_settings_for_default_caps(db_session, monkeypatch):
    """Defaults for max_per_profile and deadline_seconds come from Settings
    (#77) — env vars `MATCHING_MAX_PER_PROFILE_PER_TICK` and
    `MATCHING_TICK_DEADLINE_SECONDS` tune behaviour without a redeploy."""
    import app.config as cfg

    # Reset the settings singleton so the monkeypatched env vars take effect
    monkeypatch.setattr(cfg, "_settings", None)
    monkeypatch.setenv("MATCHING_MAX_PER_PROFILE_PER_TICK", "2")

    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id

    jobs = []
    for i in range(5):
        job = await _job_for(
            db_session,
            "airbnb",
            external_id=f"settings-{i}",
            title=f"Engineer {i}",
            apply_url=f"https://x/{i}",
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.commit()
    for j in jobs:
        await db_session.refresh(j)
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        return {
            "scores": [
                ScoreResult(
                    application_id=jc["application_id"],
                    score=0.9,
                    summary="ok",
                    rationale="ok",
                    strengths=[],
                    gaps=[],
                )
                for jc in state["jobs"]
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        # No explicit max_per_profile: should pull `2` from MATCHING_MAX_PER_PROFILE_PER_TICK
        result = await run_match_queue()

    db_session.expire_all()
    matched = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "matched",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(matched) == 2, f"settings-driven cap=2 should mean 2 scored, got {len(matched)}"
    assert result["deferred"] == 3, f"expected 3 deferred, got {result}"


@pytest.mark.asyncio
async def test_run_match_queue_caps_jobs_per_profile_per_tick(db_session):
    """A single profile must not own more than `max_per_profile` jobs in one
    score_and_match call. With batch_size=100 concentrated on one profile and
    slow Gemini latency, a single LangGraph batch can exceed Cloud Run's 300s
    wall (one-off HTTP 504 in /internal/cron/process-match-queue, 2026-05-02).
    Unprocessed apps stay pending_match with claimed_at set; the 300s lease
    in match_queue_service.next_batch makes them re-eligible next tick."""
    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id

    # Seed 8 jobs + 8 pending_match Applications for the same profile
    jobs = []
    for i in range(8):
        job = await _job_for(
            db_session,
            "airbnb",
            external_id=f"cap-{i}",
            title=f"Engineer {i}",
            apply_url=f"https://x/{i}",
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.commit()
    for j in jobs:
        await db_session.refresh(j)
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        # Stub: score every job that was sent into the graph (but only those)
        return {
            "scores": [
                ScoreResult(
                    application_id=jc["application_id"],
                    score=0.9,
                    summary="cap-test",
                    rationale="cap-test",
                    strengths=[],
                    gaps=[],
                )
                for jc in state["jobs"]
            ]
        }

    fake_graph.ainvoke = fake_invoke

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result = await run_match_queue(max_per_profile=5)

    # Cap: only 5 of the 8 claimed apps were sent to score_and_match this tick.
    # The other 3 stay pending_match with claimed_at set (300s lease).
    assert result["attempted"] == 8, "all 8 were claimed by next_batch"
    assert result["succeeded"] == 5, "exactly max_per_profile (5) were scored"
    assert result["deferred"] == 3, "3 deferred to a later tick by the per-profile cap"

    db_session.expire_all()
    matched = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "matched",
                )
            )
        )
        .scalars()
        .all()
    )
    pending = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "pending_match",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(matched) == 5
    assert len(pending) == 3
    # Deferred apps must remain claimed (claimed_at set) so the next tick's
    # next_batch call doesn't re-claim them inside the 300s lease window.
    for app in pending:
        assert app.match_claimed_at is not None, (
            "deferred apps must remain claimed for the 300s lease window"
        )


@pytest.mark.asyncio
async def test_run_match_queue_reprocesses_deferred_apps_after_lease_expiry(db_session):
    """The cap fix's load-bearing assumption: apps deferred this tick (claimed
    but unprocessed because of the per-profile cap) get reclaimed on a later
    tick once the 300s lease expires. Without this property, the cap would
    silently leak work into oblivion (#79)."""
    from datetime import UTC, datetime, timedelta

    profile = await _seed_profile(db_session, "airbnb")
    profile_id = profile.id

    # Seed 5 apps, cap=2 → 2 matched first tick, 3 deferred (claimed_at set)
    jobs = []
    for i in range(5):
        job = await _job_for(
            db_session,
            "airbnb",
            external_id=f"reproc-{i}",
            title=f"Engineer {i}",
            apply_url=f"https://x/{i}",
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.commit()
    for j in jobs:
        await db_session.refresh(j)
        await match_queue_service.enqueue_for_interested_profiles(j, db_session)

    fake_graph = MagicMock()

    async def fake_invoke(state, config=None):
        from app.agents.matching_agent import ScoreResult

        return {
            "scores": [
                ScoreResult(
                    application_id=jc["application_id"],
                    score=0.9,
                    summary="ok",
                    rationale="ok",
                    strengths=[],
                    gaps=[],
                )
                for jc in state["jobs"]
            ]
        }

    fake_graph.ainvoke = fake_invoke

    # First tick: cap=2 → 2 matched, 3 deferred
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result1 = await run_match_queue(max_per_profile=2)
    assert result1["succeeded"] == 2
    assert result1["deferred"] == 3

    # Second tick *immediately*: lease still active → next_batch claims nothing
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result2 = await run_match_queue(max_per_profile=2)
    assert result2["attempted"] == 0, (
        "lease should still be active; next_batch should claim nothing"
    )

    # Back-date the deferred apps' claimed_at past the 300s cutoff so the lease
    # is effectively expired, then run again.
    long_ago = datetime.now(UTC) - timedelta(seconds=400)
    await db_session.execute(
        sa.update(Application)
        .where(
            Application.profile_id == profile_id,
            Application.match_status == "pending_match",
        )
        .values(match_claimed_at=long_ago)
    )
    await db_session.commit()
    db_session.expire_all()

    # Third tick: lease expired → next_batch reclaims, cap=2 of the 3 process
    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result3 = await run_match_queue(max_per_profile=2)
    assert result3["succeeded"] == 2, (
        f"after lease expiry, deferred apps must be reclaimable; got {result3}"
    )
    assert result3["deferred"] == 1, "1 still deferred (5 total, 2 matched in tick1, 2 in tick3)"

    # Fourth tick: back-date again to push the last deferred over the lease line
    long_ago2 = datetime.now(UTC) - timedelta(seconds=400)
    await db_session.execute(
        sa.update(Application)
        .where(
            Application.profile_id == profile_id,
            Application.match_status == "pending_match",
        )
        .values(match_claimed_at=long_ago2)
    )
    await db_session.commit()

    with patch("app.agents.matching_agent.build_graph", return_value=fake_graph):
        result4 = await run_match_queue(max_per_profile=2)
    assert result4["succeeded"] == 1, "the last deferred app finally drains"

    # Final state: all 5 apps matched, 0 still pending
    db_session.expire_all()
    pending_left = (
        (
            await db_session.execute(
                sa.select(Application).where(
                    Application.profile_id == profile_id,
                    Application.match_status == "pending_match",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pending_left) == 0, "all originally-deferred apps must end up matched"
