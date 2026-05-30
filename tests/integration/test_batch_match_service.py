import uuid
from datetime import UTC, datetime, timedelta

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


async def add_unscored_app_for_profile(
    db_session,
    profile: UserProfile,
    *,
    title: str = "Backend Engineer",
    description: str = "Build Python APIs for marketplace systems.",
    created_at: datetime | None = None,
) -> Application:
    company_id = profile.target_company_ids[0]
    job = Job(
        source="greenhouse",
        external_id=f"batch-job-{uuid.uuid4()}",
        title=title,
        company_name="Airbnb",
        company_id=company_id,
        location="Remote - United States",
        workplace_type="remote",
        description=description,
        apply_url=f"https://example.com/job/{uuid.uuid4()}",
        is_active=True,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    app = Application(
        job_id=job.id,
        profile_id=profile.id,
        status="pending_review",
        match_score=None,
        match_strengths=[],
        match_gaps=[],
        created_at=created_at or datetime.now(UTC),
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


async def _batch_items(db_session) -> list[LLMMatchBatchItem]:
    return list((await db_session.execute(select(LLMMatchBatchItem))).scalars().all())


async def _batch_items_for_batch(
    db_session,
    batch_id: uuid.UUID,
) -> list[LLMMatchBatchItem]:
    return list(
        (
            await db_session.execute(
                select(LLMMatchBatchItem).where(LLMMatchBatchItem.batch_id == batch_id)
            )
        )
        .scalars()
        .all()
    )


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
async def test_build_prioritizes_high_signal_survivors_before_newer_weak_matches(
    db_session,
    monkeypatch,
):
    import app.config as cfg
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    monkeypatch.setenv("BATCH_MATCH_MAX_ITEMS_PER_BATCH", "2")
    monkeypatch.setattr(cfg, "_settings", None)
    profile, _ = await seed_profile_with_unscored_apps(db_session, count=0)
    profile.target_roles = ["Senior Backend Engineer"]
    profile.seniority = "senior"
    db_session.add(profile)
    await db_session.commit()

    weak_newer = await add_unscored_app_for_profile(
        db_session,
        profile,
        title="Operations Analyst",
        description="Coordinate spreadsheets and internal process reporting.",
        created_at=datetime.now(UTC),
    )
    weak_second = await add_unscored_app_for_profile(
        db_session,
        profile,
        title="Program Coordinator",
        description="Track internal projects and organize status meetings.",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    strong_older = await add_unscored_app_for_profile(
        db_session,
        profile,
        title="Senior Backend Engineer",
        description="Build Python APIs, PostgreSQL services, and distributed platform systems.",
        created_at=datetime.now(UTC) - timedelta(days=1),
    )

    provider = FakeBatchMatchProvider(ready=False)

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=provider)

    assert result.selected == 3
    assert result.submitted == 2
    submitted_ids = [
        job["application_id"]
        for request in provider.submitted_requests
        for job in request["jobs"]
    ]
    assert str(strong_older.id) in submitted_ids
    assert len(submitted_ids) == 2
    assert str(weak_newer.id) not in submitted_ids or str(weak_second.id) not in submitted_ids


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
async def test_poll_exception_fails_batch_and_requeues(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    class PollRaises(FakeBatchMatchProvider):
        async def poll(self, *, provider_batch_id: str):
            raise RuntimeError("poll boom")

    profile, _ = await seed_profile_with_unscored_apps(db_session, count=2)
    first_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-1")
    await run_batch_match_tick(db_session, profile_id=profile.id, provider=first_provider)
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

    result = await run_batch_match_tick(
        db_session,
        profile_id=profile.id,
        provider=PollRaises(ready=False, provider_batch_id="batch-1"),
    )

    assert result.requeued is True
    assert result.retryable_failed == 2

    await db_session.refresh(original_batch)
    assert original_batch.status == "failed"
    assert original_batch.last_error == "provider poll failed: poll boom"

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"retryable_failed"}


@pytest.mark.asyncio
async def test_stale_building_batch_is_failed_and_rebuilt(db_session):
    from app.services.batch_match_provider import FakeBatchMatchProvider
    from app.services.batch_match_service import run_batch_match_tick

    profile, apps = await seed_profile_with_unscored_apps(db_session, count=1)
    stale_at = datetime.now(UTC) - timedelta(seconds=120)
    stale_batch = LLMMatchBatch(
        profile_id=profile.id,
        provider="fake",
        model="gemini-2.5-flash",
        prompt_version="batch-match-v1",
        status="building",
        created_at=stale_at,
        updated_at=stale_at,
    )
    db_session.add(stale_batch)
    await db_session.flush()
    db_session.add(
        LLMMatchBatchItem(
            batch_id=stale_batch.id,
            application_id=apps[0].id,
            provider_request_key="request-0001",
            request_hash="stale",
        )
    )
    await db_session.commit()

    result = await run_batch_match_tick(
        db_session,
        profile_id=profile.id,
        provider=FakeBatchMatchProvider(ready=False, provider_batch_id="batch-2"),
    )

    assert result.retryable_failed == 1
    assert result.submitted == 1

    batches = (
        await db_session.execute(
            select(LLMMatchBatch).order_by(LLMMatchBatch.created_at.asc())
        )
    ).scalars().all()
    assert [batch.status for batch in batches] == ["failed", "submitted"]


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
async def test_ready_import_submits_remaining_unscored_apps_in_same_tick(db_session):
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

    new_app = await add_unscored_app_for_profile(
        db_session,
        profile,
        title="Platform Engineer",
    )
    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-2",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.82,
                            summary="Backend API role",
                            rationale="Strong Python match",
                            strengths=["Python"],
                            gaps=[],
                        )
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 1
    assert result.selected == 1
    assert result.submitted == 1
    assert result.retryable_failed == 0
    assert len(second_provider.submitted_requests) == 1
    assert second_provider.submitted_requests[0]["jobs"][0]["application_id"] == str(new_app.id)

    old_app = await db_session.get(Application, apps[0].id)
    refreshed_new_app = await db_session.get(Application, new_app.id)
    assert old_app is not None
    assert refreshed_new_app is not None
    assert old_app.match_score == 0.82
    assert refreshed_new_app.match_score is None

    batches = list((await db_session.execute(select(LLMMatchBatch))).scalars().all())
    assert sorted(batch.status for batch in batches) == ["done", "submitted"]


@pytest.mark.asyncio
async def test_duplicate_provider_result_ids_mark_request_retryable_without_partial_import(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                            application_id=str(apps[0].id),
                            score=0.7,
                            summary="Duplicate",
                            rationale="Duplicate result",
                            strengths=["APIs"],
                            gaps=[],
                        ),
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 0
    assert result.terminal_failed == 2
    for app in apps:
        refreshed = await db_session.get(Application, app.id)
        assert refreshed is not None
        assert refreshed.match_score is None

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"terminal_failed"}
    assert {item.error for item in items} == {"provider returned duplicate application_id"}


@pytest.mark.asyncio
async def test_unknown_provider_result_id_marks_request_terminal_without_retry_loop(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                            application_id=str(uuid.uuid4()),
                            score=0.7,
                            summary="Unknown",
                            rationale="Not from this request",
                            strengths=["APIs"],
                            gaps=[],
                        ),
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 0
    assert result.terminal_failed == 2
    for app in apps:
        refreshed = await db_session.get(Application, app.id)
        assert refreshed is not None
        assert refreshed.match_score is None

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"terminal_failed"}
    assert {item.error for item in items} == {"provider returned unknown application_id"}

    retry_provider = FakeBatchMatchProvider(ready=False, provider_batch_id="batch-2")
    retry_result = await run_batch_match_tick(
        db_session,
        profile_id=profile.id,
        provider=retry_provider,
    )

    assert retry_result.selected == 0
    assert retry_result.submitted == 0
    assert retry_provider.submitted_requests == []


@pytest.mark.asyncio
async def test_unknown_provider_request_key_marks_batch_retryable_without_partial_import(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

    second_provider = FakeBatchMatchProvider(
        ready=True,
        provider_batch_id="batch-1",
        output=ProviderBatchOutput(
            requests=[
                ProviderRequestResult(
                    request_key="request-unknown",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.8,
                            summary="Backend API role",
                            rationale="Strong Python match",
                            strengths=["Python"],
                            gaps=[],
                        )
                    ],
                ),
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[1].id),
                            score=0.7,
                            summary="Known request",
                            rationale="Should not import after correlation error",
                            strengths=["APIs"],
                            gaps=[],
                        )
                    ],
                ),
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 0
    assert result.terminal_failed == 2
    for app in apps:
        refreshed = await db_session.get(Application, app.id)
        assert refreshed is not None
        assert refreshed.match_score is None

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"terminal_failed"}
    assert {item.error for item in items} == {"provider returned unknown request_key"}


@pytest.mark.asyncio
async def test_duplicate_provider_result_ids_split_across_requests_mark_retryable(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                        )
                    ],
                ),
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[0].id),
                            score=0.7,
                            summary="Duplicate",
                            rationale="Duplicate result",
                            strengths=["APIs"],
                            gaps=[],
                        )
                    ],
                ),
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 0
    assert result.terminal_failed == 2
    for app in apps:
        refreshed = await db_session.get(Application, app.id)
        assert refreshed is not None
        assert refreshed.match_score is None

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"terminal_failed"}
    assert {item.error for item in items} == {"provider returned duplicate application_id"}


@pytest.mark.asyncio
async def test_duplicate_provider_request_blocks_mark_retryable_without_partial_import(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                        )
                    ],
                ),
                ProviderRequestResult(
                    request_key="request-0001",
                    results=[
                        ProviderJobResult(
                            application_id=str(apps[1].id),
                            score=0.7,
                            summary="Backend platform role",
                            rationale="Different result in duplicate block",
                            strengths=["APIs"],
                            gaps=[],
                        )
                    ],
                ),
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 0
    assert result.terminal_failed == 2
    for app in apps:
        refreshed = await db_session.get(Application, app.id)
        assert refreshed is not None
        assert refreshed.match_score is None

    items = await _batch_items_for_batch(db_session, original_batch.id)
    assert {item.status for item in items} == {"terminal_failed"}
    assert {item.error for item in items} == {"provider returned duplicate request_key"}


@pytest.mark.asyncio
async def test_malformed_provider_result_fields_mark_item_retryable_without_score(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                            summary=123,  # type: ignore[arg-type]
                            rationale="Strong Python match",
                            strengths=["Python"],
                            gaps=[],
                        )
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 1
    refreshed = await db_session.get(Application, apps[0].id)
    assert refreshed is not None
    assert refreshed.match_score is None

    item = (await _batch_items_for_batch(db_session, original_batch.id))[0]
    assert item.status == "retryable_failed"
    assert item.error == "provider returned malformed summary"


@pytest.mark.asyncio
async def test_null_provider_result_rationale_marks_item_retryable_without_score(
    db_session,
):
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
    original_batch = (await db_session.execute(select(LLMMatchBatch))).scalar_one()

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
                            rationale=None,  # type: ignore[arg-type]
                            strengths=["Python"],
                            gaps=[],
                        )
                    ],
                )
            ]
        ),
    )

    result = await run_batch_match_tick(db_session, profile_id=profile.id, provider=second_provider)

    assert result.imported == 0
    assert result.retryable_failed == 1
    refreshed = await db_session.get(Application, apps[0].id)
    assert refreshed is not None
    assert refreshed.match_score is None

    item = (await _batch_items_for_batch(db_session, original_batch.id))[0]
    assert item.status == "retryable_failed"
    assert item.error == "provider returned malformed rationale"


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
