import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.llm_match_batch import LLMMatchBatch, LLMMatchBatchItem
from app.models.user import User
from app.models.user_profile import UserProfile


async def seed_profile_with_unscored_apps(
    db_session,
    *,
    count: int,
    app_status: str = "pending_review",
) -> tuple[UserProfile, list[Application]]:
    user = User(id=uuid.uuid4(), email=f"batch-match-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    company = Company(
        canonical_name="Airbnb",
        normalized_key=f"airbnb-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "airbnb"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        full_name="Batch Match User",
        base_resume_md="Python backend engineer with API and platform experience.",
        target_company_ids=[company.id],
        target_locations=["San Francisco"],
        remote_ok=True,
        search_active=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)

    apps: list[Application] = []
    for index in range(count):
        job = Job(
            source="greenhouse",
            external_id=f"batch-job-{uuid.uuid4()}",
            title=f"Backend Engineer {index}",
            company_name="Airbnb",
            company_id=company.id,
            location="Remote - United States",
            workplace_type="remote",
            description="Build Python APIs for marketplace systems.",
            apply_url=f"https://example.com/job/{index}",
            is_active=True,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        app = Application(
            job_id=job.id,
            profile_id=profile.id,
            status=app_status,
            match_score=None,
            match_strengths=[],
            match_gaps=[],
        )
        db_session.add(app)
        await db_session.commit()
        await db_session.refresh(app)
        apps.append(app)

    return profile, apps


async def seed_profile_with_non_us_app(db_session) -> tuple[UserProfile, list[Application]]:
    profile, apps = await seed_profile_with_unscored_apps(db_session, count=1)
    job = await db_session.get(Job, apps[0].job_id)
    assert job is not None
    job.location = "Toronto, Canada"
    job.workplace_type = "remote"
    job.description = "Remote role open to candidates based in Canada."
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(apps[0])
    return profile, apps


async def _batch_items(db_session) -> list[LLMMatchBatchItem]:
    return list((await db_session.execute(select(LLMMatchBatchItem))).scalars().all())


@pytest.mark.asyncio
async def test_build_submits_batch_for_profile_unscored_apps(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=3)
    provider = FakeBatchMatchProvider(ready=False)

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=provider)

    assert result.selected == 3
    assert result.submitted == 3
    assert result.imported == 0
    assert len(provider.submitted_requests) == 1
    assert len(provider.submitted_requests[0]["jobs"]) == 3

    batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()
    assert batch.status == "submitted"
    assert batch.provider_batch_id == provider.provider_batch_id
    assert batch.submitted_at is not None
    assert batch.next_poll_at is not None

    items = await _batch_items(db_session)
    assert len(items) == 3
    assert {item.application_id for item in items} == {app.id for app in apps}


@pytest.mark.asyncio
async def test_deterministic_reject_is_not_submitted(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_non_us_app(db_session)
    provider = FakeBatchMatchProvider(ready=False)

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=provider)

    assert result.deterministic_rejected == 1
    assert result.submitted == 0
    assert provider.submitted_requests == []
    assert await _batch_items(db_session) == []

    refreshed = await db_session.get(Application, apps[0].id)
    assert refreshed is not None
    assert refreshed.status == "auto_rejected"
    assert refreshed.match_score is not None
    assert refreshed.match_summary == "Deterministic mismatch: non-US position"


@pytest.mark.asyncio
async def test_poll_requeues_when_provider_is_not_ready(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, _ = await seed_profile_with_unscored_apps(db_session, count=1)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)

    second_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    result = await run_batch_match_tick(
        db_session,
        profile_id=profile.id,
        provider=second_provider,
    )

    assert result.requeued is True
    batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()
    assert batch.status == "submitted"
    assert batch.last_polled_at is not None
    assert batch.next_poll_at is not None


@pytest.mark.asyncio
async def test_import_partial_provider_output(db_session):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        ProviderBatchOutput,
        ProviderJobResult,
        ProviderRequestResult,
    )
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=2)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)

    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-1",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.8,
                            summary="Backend API role",
                            rationale="Strong Python match",
                            strengths=["Python"],
                            gaps=[],
                        ),
                        ProviderJobResult(
                            application_id=str(apps[1].id),
                            score=None,
                            summary="",
                            rationale="Provider returned null score",
                            strengths=[],
                            gaps=[],
                        ),
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 1
    assert result.retryable_failed == 1

    imported = await db_session.get(Application, apps[0].id)
    retryable = await db_session.get(Application, apps[1].id)
    assert imported is not None
    assert retryable is not None
    assert imported.match_score == 0.8
    assert imported.match_summary == "Backend API role"
    assert retryable.match_score is None


@pytest.mark.asyncio
async def test_import_skips_existing_scored_application(db_session):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        ProviderBatchOutput,
        ProviderJobResult,
        ProviderRequestResult,
    )
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=1)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)

    app = await db_session.get(Application, apps[0].id)
    assert app is not None
    app.match_score = 0.91
    app.match_summary = "Existing score"
    db_session.add(app)
    await db_session.commit()

    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-1",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.2,
                            summary="Provider score",
                            rationale="Provider rationale",
                            strengths=[],
                            gaps=["Gap"],
                        )
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 1
    refreshed = await db_session.get(Application, apps[0].id)
    assert refreshed is not None
    assert refreshed.match_score == 0.91
    assert refreshed.match_summary == "Existing score"


@pytest.mark.asyncio
async def test_below_threshold_import_preserves_user_owned_status(db_session):
    from app.services.batch_match_provider import (
        FakeBatchMatchProvider,
        ProviderBatchOutput,
        ProviderJobResult,
        ProviderRequestResult,
    )
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=1)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)

    app = await db_session.get(Application, apps[0].id)
    assert app is not None
    app.status = "applied"
    db_session.add(app)
    await db_session.commit()

    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-1",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.2,
                            summary="Weak fit",
                            rationale="Below threshold",
                            strengths=[],
                            gaps=["Mismatch"],
                        )
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 1
    refreshed = await db_session.get(Application, apps[0].id)
    assert refreshed is not None
    assert refreshed.match_score == 0.2
    assert refreshed.status == "applied"
