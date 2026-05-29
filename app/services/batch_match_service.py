from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.config import get_settings
from app.models.application import Application
from app.models.job import Job
from app.models.llm_match_batch import (
    ACTIVE_BATCH_STATUSES,
    BATCH_STATUS_BUILDING,
    BATCH_STATUS_DONE,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_IMPORTING,
    BATCH_STATUS_SUBMITTED,
    ITEM_STATUS_IMPORTED,
    ITEM_STATUS_RETRYABLE_FAILED,
    ITEM_STATUS_SUBMITTED,
    ITEM_STATUS_TERMINAL_FAILED,
    LLMMatchBatch,
    LLMMatchBatchItem,
)
from app.models.user_profile import UserProfile
from app.services.batch_match_packing import (
    BatchJobContext,
    PackedProviderRequest,
    build_request_hash,
    pack_provider_requests,
)
from app.services.batch_match_provider import (
    BatchMatchProvider,
    ProviderBatchOutput,
    ProviderJobResult,
)
from app.services.match_service import (
    DISPLAY_JOB_MAX_AGE_DAYS,
    DeterministicRejectionFields,
    deterministic_rejection_fields,
    format_profile_text,
)
from app.services.profile_service import get_skills, get_work_experiences


@dataclass(frozen=True)
class BatchMatchTickResult:
    selected: int = 0
    deterministic_rejected: int = 0
    submitted: int = 0
    imported: int = 0
    retryable_failed: int = 0
    terminal_failed: int = 0
    requeued: bool = False


@dataclass
class _ImportCounters:
    imported: int = 0
    retryable_failed: int = 0
    terminal_failed: int = 0


_TERMINAL_PROVIDER_CORRELATION_ERRORS = {"provider returned unknown application_id"}


async def run_batch_match_tick(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    provider: BatchMatchProvider,
) -> BatchMatchTickResult:
    active = await _get_active_batch(session, profile_id)
    if active is not None and active.status in (
        BATCH_STATUS_SUBMITTED,
        BATCH_STATUS_IMPORTING,
    ):
        import_result = await _poll_and_import(session, batch=active, provider=provider)
        if import_result.requeued or active.status == BATCH_STATUS_FAILED:
            return import_result
        if active.status != BATCH_STATUS_DONE:
            return import_result
        build_result = await _build_and_submit(
            session,
            profile_id=profile_id,
            provider=provider,
        )
        return _combine_tick_results(import_result, build_result)
    if active is not None and active.status == BATCH_STATUS_BUILDING:
        if _is_stale_building_batch(active):
            fail_result = await _fail_batch(
                session,
                batch=active,
                error="stale building batch",
                requeued=False,
            )
            build_result = await _build_and_submit(
                session,
                profile_id=profile_id,
                provider=provider,
            )
            return _combine_tick_results(fail_result, build_result)
        return BatchMatchTickResult(requeued=True)
    if active is not None:
        return BatchMatchTickResult(requeued=True)
    return await _build_and_submit(session, profile_id=profile_id, provider=provider)


def _is_stale_building_batch(batch: LLMMatchBatch) -> bool:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(
        seconds=max(settings.batch_match_poll_interval_seconds, 60)
    )
    last_change = batch.updated_at or batch.created_at
    return last_change < cutoff


def _combine_tick_results(
    import_result: BatchMatchTickResult,
    build_result: BatchMatchTickResult,
) -> BatchMatchTickResult:
    return BatchMatchTickResult(
        selected=build_result.selected,
        deterministic_rejected=build_result.deterministic_rejected,
        submitted=build_result.submitted,
        imported=import_result.imported,
        retryable_failed=import_result.retryable_failed,
        terminal_failed=import_result.terminal_failed,
        requeued=build_result.requeued,
    )


async def _get_active_batch(
    session: AsyncSession,
    profile_id: uuid.UUID,
) -> LLMMatchBatch | None:
    result = await session.execute(
        select(LLMMatchBatch)
        .where(
            LLMMatchBatch.profile_id == profile_id,
            col(LLMMatchBatch.status).in_(ACTIVE_BATCH_STATUSES),
        )
        .order_by(LLMMatchBatch.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_and_submit(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    provider: BatchMatchProvider,
) -> BatchMatchTickResult:
    settings = get_settings()
    profile = await session.get(UserProfile, profile_id)
    if profile is None:
        return BatchMatchTickResult()

    rows = await _select_unscored_application_rows(
        session,
        profile_id=profile_id,
        limit=settings.batch_match_max_items_per_batch,
    )
    selected = len(rows)
    if not rows:
        return BatchMatchTickResult()

    deterministic_rejected = 0
    survivors: list[BatchJobContext] = []
    for app, job in rows:
        if _apply_deterministic_reject_if_needed(app, profile, job):
            deterministic_rejected += 1
            continue
        survivors.append(_job_context(app, job))

    if not survivors:
        await session.commit()
        return BatchMatchTickResult(
            selected=selected,
            deterministic_rejected=deterministic_rejected,
        )

    profile_text = format_profile_text(
        profile,
        await get_skills(profile.id, session),
        await get_work_experiences(profile.id, session),
    )
    groups = pack_provider_requests(
        profile_text=profile_text,
        jobs=survivors,
        max_apps_per_request=settings.batch_match_max_apps_per_request,
        max_request_chars=settings.batch_match_max_request_chars,
    )
    requests = [_provider_request(group, profile_text=profile_text) for group in groups]
    batch = await _create_batch_with_items(
        session,
        profile_id=profile_id,
        profile_text=profile_text,
        groups=groups,
    )
    await session.commit()

    now = datetime.now(UTC)
    try:
        provider_batch_id = await provider.submit(
            requests=requests,
            display_name=f"batch-match-{batch.id}",
        )
    except Exception as exc:
        await _fail_batch(session, batch=batch, error=str(exc))
        raise
    batch.provider_batch_id = provider_batch_id
    batch.status = BATCH_STATUS_SUBMITTED
    batch.submitted_at = now
    batch.next_poll_at = now + timedelta(seconds=settings.batch_match_poll_interval_seconds)
    batch.updated_at = now
    session.add(batch)
    await session.commit()
    return BatchMatchTickResult(
        selected=selected,
        deterministic_rejected=deterministic_rejected,
        submitted=len(survivors),
    )


async def _poll_and_import(
    session: AsyncSession,
    *,
    batch: LLMMatchBatch,
    provider: BatchMatchProvider,
) -> BatchMatchTickResult:
    settings = get_settings()
    now = datetime.now(UTC)
    if not batch.provider_batch_id:
        return await _fail_batch(session, batch=batch, error="missing provider_batch_id")

    try:
        status = await provider.poll(provider_batch_id=batch.provider_batch_id)
    except Exception as exc:
        return await _fail_batch(
            session,
            batch=batch,
            error=f"provider poll failed: {exc}",
            requeued=True,
        )
    batch.last_polled_at = now
    batch.updated_at = now
    if status.failed:
        return await _fail_batch(
            session,
            batch=batch,
            error=status.error or "provider failed",
            requeued=True,
        )
    if not status.ready:
        batch.next_poll_at = now + timedelta(seconds=settings.batch_match_poll_interval_seconds)
        session.add(batch)
        await session.commit()
        return BatchMatchTickResult(requeued=True)

    batch.status = BATCH_STATUS_IMPORTING
    batch.next_poll_at = None
    session.add(batch)
    await session.commit()

    try:
        output = await provider.fetch_output(provider_batch_id=batch.provider_batch_id)
    except Exception as exc:
        return await _fail_batch(
            session,
            batch=batch,
            error=f"provider output fetch failed: {exc}",
            requeued=True,
        )
    counters = await _import_provider_output(session, batch=batch, output=output)
    await _finish_batch_if_drained(session, batch=batch)
    await session.commit()
    return BatchMatchTickResult(
        imported=counters.imported,
        retryable_failed=counters.retryable_failed,
        terminal_failed=counters.terminal_failed,
    )


async def _fail_batch(
    session: AsyncSession,
    *,
    batch: LLMMatchBatch,
    error: str,
    requeued: bool = False,
) -> BatchMatchTickResult:
    now = datetime.now(UTC)
    await _mark_submitted_items_retryable(session, batch.id, error)
    batch.status = BATCH_STATUS_FAILED
    batch.last_error = error
    batch.completed_at = now
    batch.next_poll_at = None
    batch.updated_at = now
    session.add(batch)
    await session.commit()
    return await _result_from_batch(session, batch, requeued=requeued)


async def _select_unscored_application_rows(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    limit: int,
) -> list[tuple[Application, Job]]:
    posted_cutoff = datetime.now(UTC) - timedelta(days=DISPLAY_JOB_MAX_AGE_DAYS)
    blocked_item_app_ids = (
        select(LLMMatchBatchItem.application_id)
        .join(LLMMatchBatch, LLMMatchBatchItem.batch_id == LLMMatchBatch.id)
        .where(
            or_(
                and_(
                    LLMMatchBatchItem.status == ITEM_STATUS_SUBMITTED,
                    col(LLMMatchBatch.status).in_(ACTIVE_BATCH_STATUSES),
                ),
                LLMMatchBatchItem.status == ITEM_STATUS_TERMINAL_FAILED,
            )
        )
    )
    result = await session.execute(
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .where(
            Application.profile_id == profile_id,
            col(Application.match_score).is_(None),
            col(Application.status).in_(("pending_review", "auto_rejected")),
            Job.is_active.is_(True),
            (col(Job.posted_at).is_(None)) | (Job.posted_at >= posted_cutoff),
            col(Application.id).notin_(blocked_item_app_ids),
        )
        .order_by(
            Job.posted_at.desc().nullslast(),
            Application.created_at.desc(),
            Application.id.asc(),
        )
        .limit(limit)
    )
    return [(app, job) for app, job in result.all()]


async def _create_batch_with_items(
    session: AsyncSession,
    *,
    profile_id: uuid.UUID,
    profile_text: str,
    groups: list[PackedProviderRequest],
) -> LLMMatchBatch:
    settings = get_settings()
    batch = LLMMatchBatch(
        profile_id=profile_id,
        provider=settings.batch_match_provider or "fake",
        model=settings.llm_matching_model,
        prompt_version=settings.batch_match_prompt_version,
    )
    session.add(batch)
    await session.flush()
    for group in groups:
        for job in group.jobs:
            session.add(
                LLMMatchBatchItem(
                    batch_id=batch.id,
                    application_id=job.application_id,
                    provider_request_key=group.request_key,
                    request_hash=build_request_hash(
                        prompt_version=settings.batch_match_prompt_version,
                        model=settings.llm_matching_model,
                        profile_text=profile_text,
                        job=job,
                    ),
                )
            )
    return batch


def _apply_deterministic_reject_if_needed(
    app: Application,
    profile: UserProfile,
    job: Job,
) -> bool:
    settings = get_settings()
    fields = deterministic_rejection_fields(profile, job, settings.match_score_threshold)
    if fields is None:
        return False
    _apply_score_fields(app, fields)
    return True


def _apply_score_fields(app: Application, fields: DeterministicRejectionFields) -> None:
    app.match_score = fields["score"]
    app.match_summary = fields["summary"]
    app.match_rationale = fields["rationale"]
    app.match_strengths = fields["strengths"]
    app.match_gaps = fields["gaps"]
    if fields["score"] < get_settings().match_score_threshold and app.status == "pending_review":
        app.status = "auto_rejected"
    app.updated_at = datetime.now(UTC)


def _job_context(app: Application, job: Job) -> BatchJobContext:
    return BatchJobContext(
        application_id=app.id,
        title=job.title,
        company=job.company_name,
        location=job.location,
        workplace_type=job.workplace_type,
        description=job.description or job.description_raw or "",
    )


def _provider_request(group: PackedProviderRequest, *, profile_text: str) -> dict:
    settings = get_settings()
    return {
        "request_key": group.request_key,
        "model": settings.llm_matching_model,
        "prompt_version": settings.batch_match_prompt_version,
        "profile_text": profile_text,
        "jobs": [
            {
                "application_id": str(job.application_id),
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "workplace_type": job.workplace_type,
                "description": job.description,
            }
            for job in group.jobs
        ],
    }


async def _import_provider_output(
    session: AsyncSession,
    *,
    batch: LLMMatchBatch,
    output: ProviderBatchOutput,
) -> _ImportCounters:
    counters = _ImportCounters()
    items = await _items_by_provider_key(session, batch.id)
    batch_correlation_error, request_correlation_errors = (
        _provider_output_correlation_errors(items, output)
    )
    if batch_correlation_error is not None:
        counters.retryable_failed += _mark_items_retryable(
            items.values(),
            batch_correlation_error,
        )
        return counters
    for request_key, error in request_correlation_errors.items():
        request_items = _items_by_request_key(items, request_key)
        if _is_terminal_provider_correlation_error(error):
            counters.terminal_failed += _mark_items_terminal(request_items, error)
        else:
            counters.retryable_failed += _mark_items_retryable(request_items, error)

    seen_item_ids: set[uuid.UUID] = set()

    for request in output.requests:
        request_items = _items_by_request_key(items, request.request_key)
        if request.request_key in request_correlation_errors:
            continue
        if request.error:
            for item in request_items:
                if item.status == ITEM_STATUS_SUBMITTED:
                    _mark_item_retryable(item, request.error)
                    counters.retryable_failed += 1
                    seen_item_ids.add(item.id)
            continue
        correlation_error = _request_correlation_error(request_items, request.results)
        if correlation_error is not None:
            mark_item = (
                _mark_item_terminal
                if _is_terminal_provider_correlation_error(correlation_error)
                else _mark_item_retryable
            )
            for item in request_items:
                if item.status == ITEM_STATUS_SUBMITTED:
                    mark_item(item, correlation_error)
                    if _is_terminal_provider_correlation_error(correlation_error):
                        counters.terminal_failed += 1
                    else:
                        counters.retryable_failed += 1
                    seen_item_ids.add(item.id)
            continue
        for result in request.results:
            item = _find_result_item(items, request.request_key, result)
            if item is None or item.status != ITEM_STATUS_SUBMITTED:
                continue
            seen_item_ids.add(item.id)
            await _import_result_for_item(session, item, result, counters)

    for item in items.values():
        if item.status == ITEM_STATUS_SUBMITTED and item.id not in seen_item_ids:
            _mark_item_retryable(item, "provider result missing")
            counters.retryable_failed += 1
    return counters


async def _items_by_provider_key(
    session: AsyncSession,
    batch_id: uuid.UUID,
) -> dict[tuple[str, uuid.UUID], LLMMatchBatchItem]:
    result = await session.execute(
        select(LLMMatchBatchItem).where(LLMMatchBatchItem.batch_id == batch_id)
    )
    return {
        (item.provider_request_key, item.application_id): item
        for item in result.scalars().all()
    }


def _items_by_request_key(
    items: dict[tuple[str, uuid.UUID], LLMMatchBatchItem],
    request_key: str,
) -> list[LLMMatchBatchItem]:
    return [item for (key, _), item in items.items() if key == request_key]


def _provider_output_correlation_errors(
    items: dict[tuple[str, uuid.UUID], LLMMatchBatchItem],
    output: ProviderBatchOutput,
) -> tuple[str | None, dict[str, str]]:
    request_application_ids: dict[str, set[uuid.UUID]] = {}
    for request_key, application_id in items:
        request_application_ids.setdefault(request_key, set()).add(application_id)

    request_errors: dict[str, str] = {}
    seen_request_keys: set[str] = set()
    seen_results: set[tuple[str, uuid.UUID]] = set()
    for request in output.requests:
        expected_application_ids = request_application_ids.get(request.request_key)
        if expected_application_ids is None:
            return "provider returned unknown request_key", {}
        repeated_request_key = request.request_key in seen_request_keys
        seen_request_keys.add(request.request_key)

        for result in request.results:
            try:
                application_id = uuid.UUID(str(getattr(result, "application_id", "")))
            except (TypeError, ValueError):
                request_errors.setdefault(
                    request.request_key,
                    "provider returned unknown application_id",
                )
                continue
            if application_id not in expected_application_ids:
                request_errors.setdefault(
                    request.request_key,
                    "provider returned unknown application_id",
                )
                continue
            seen_key = (request.request_key, application_id)
            if seen_key in seen_results:
                request_errors.setdefault(
                    request.request_key,
                    "provider returned duplicate application_id",
                )
                continue
            seen_results.add(seen_key)
        if repeated_request_key and request.request_key not in request_errors:
            request_errors[request.request_key] = "provider returned duplicate request_key"
    return None, request_errors


def _request_correlation_error(
    request_items: list[LLMMatchBatchItem],
    results: list[ProviderJobResult],
) -> str | None:
    request_application_ids = {item.application_id for item in request_items}
    seen_application_ids: set[uuid.UUID] = set()
    for result in results:
        try:
            application_id = uuid.UUID(str(getattr(result, "application_id", "")))
        except (TypeError, ValueError):
            return "provider returned unknown application_id"
        if application_id not in request_application_ids:
            return "provider returned unknown application_id"
        if application_id in seen_application_ids:
            return "provider returned duplicate application_id"
        seen_application_ids.add(application_id)
    return None


def _find_result_item(
    items: dict[tuple[str, uuid.UUID], LLMMatchBatchItem],
    request_key: str,
    result: ProviderJobResult,
) -> LLMMatchBatchItem | None:
    try:
        application_id = uuid.UUID(str(getattr(result, "application_id", "")))
    except (TypeError, ValueError):
        return None
    return items.get((request_key, application_id))


async def _import_result_for_item(
    session: AsyncSession,
    item: LLMMatchBatchItem,
    result: ProviderJobResult,
    counters: _ImportCounters,
) -> None:
    result_error = _result_validation_error(result)
    provider_error = getattr(result, "error", None)
    if provider_error or result_error:
        _mark_item_retryable(
            item,
            str(provider_error or result_error or "provider result invalid"),
        )
        counters.retryable_failed += 1
        return

    app = await session.get(Application, item.application_id)
    if app is None:
        _mark_item_terminal(item, "application missing")
        counters.terminal_failed += 1
        return

    job = await session.get(Job, app.job_id)
    profile = await session.get(UserProfile, app.profile_id)
    if job is None or profile is None:
        _mark_item_terminal(item, "domain row missing")
        counters.terminal_failed += 1
        return
    if app.match_score is not None:
        _apply_item_imported(item, result)
        counters.imported += 1
        return

    deterministic_fields = deterministic_rejection_fields(
        profile,
        job,
        get_settings().match_score_threshold,
    )
    if deterministic_fields is not None:
        _apply_score_fields(app, deterministic_fields)
        _apply_item_fields(item, deterministic_fields)
        counters.imported += 1
        session.add(app)
        return

    _apply_item_imported(item, result)
    counters.imported += 1
    app.match_score = item.score
    app.match_summary = result.summary
    app.match_rationale = result.rationale
    app.match_strengths = list(result.strengths)
    app.match_gaps = list(result.gaps)
    app.updated_at = datetime.now(UTC)
    settings = get_settings()
    if item.score < settings.match_score_threshold and app.status == "pending_review":
        app.status = "auto_rejected"
    session.add(app)


def _apply_item_imported(item: LLMMatchBatchItem, result: ProviderJobResult) -> None:
    item.score = float(result.score)
    item.summary = result.summary
    item.rationale = result.rationale
    item.strengths = list(result.strengths)
    item.gaps = list(result.gaps)
    item.status = ITEM_STATUS_IMPORTED
    item.error = None
    item.updated_at = datetime.now(UTC)


def _apply_item_fields(
    item: LLMMatchBatchItem,
    fields: DeterministicRejectionFields,
) -> None:
    item.score = fields["score"]
    item.summary = fields["summary"]
    item.rationale = fields["rationale"]
    item.strengths = list(fields["strengths"])
    item.gaps = list(fields["gaps"])
    item.status = ITEM_STATUS_IMPORTED
    item.error = None
    item.updated_at = datetime.now(UTC)


def _result_validation_error(result: ProviderJobResult) -> str | None:
    score_error = _score_validation_error(getattr(result, "score", None))
    if score_error is not None:
        return score_error
    if not isinstance(getattr(result, "summary", None), str):
        return "provider returned malformed summary"
    if not isinstance(getattr(result, "rationale", None), str):
        return "provider returned malformed rationale"
    if not _is_string_sequence(getattr(result, "strengths", None)):
        return "provider returned malformed strengths"
    if not _is_string_sequence(getattr(result, "gaps", None)):
        return "provider returned malformed gaps"
    return None


def _score_validation_error(score: object) -> str | None:
    if score is None:
        return "provider returned null score"
    if isinstance(score, bool) or not isinstance(score, int | float):
        return "provider returned malformed score"
    if score < 0 or score > 1:
        return "provider returned out-of-range score"
    return None


def _is_string_sequence(value: object) -> bool:
    return isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    )


def _mark_item_retryable(item: LLMMatchBatchItem, error: str) -> None:
    item.status = ITEM_STATUS_RETRYABLE_FAILED
    item.error = error
    item.updated_at = datetime.now(UTC)


def _mark_items_retryable(
    items: Iterable[LLMMatchBatchItem],
    error: str,
) -> int:
    count = 0
    for item in items:
        if item.status == ITEM_STATUS_SUBMITTED:
            _mark_item_retryable(item, error)
            count += 1
    return count


def _is_terminal_provider_correlation_error(error: str) -> bool:
    return error in _TERMINAL_PROVIDER_CORRELATION_ERRORS


def _mark_item_terminal(item: LLMMatchBatchItem, error: str) -> None:
    item.status = ITEM_STATUS_TERMINAL_FAILED
    item.error = error
    item.updated_at = datetime.now(UTC)


def _mark_items_terminal(
    items: Iterable[LLMMatchBatchItem],
    error: str,
) -> int:
    count = 0
    for item in items:
        if item.status == ITEM_STATUS_SUBMITTED:
            _mark_item_terminal(item, error)
            count += 1
    return count


async def _mark_submitted_items_retryable(
    session: AsyncSession,
    batch_id: uuid.UUID,
    error: str,
) -> None:
    result = await session.execute(
        select(LLMMatchBatchItem).where(
            LLMMatchBatchItem.batch_id == batch_id,
            LLMMatchBatchItem.status == ITEM_STATUS_SUBMITTED,
        )
    )
    for item in result.scalars().all():
        _mark_item_retryable(item, error)


async def _finish_batch_if_drained(session: AsyncSession, *, batch: LLMMatchBatch) -> None:
    remaining = (
        await session.execute(
            select(LLMMatchBatchItem.id)
            .where(
                LLMMatchBatchItem.batch_id == batch.id,
                LLMMatchBatchItem.status == ITEM_STATUS_SUBMITTED,
            )
            .limit(1)
        )
    ).first()
    if remaining is not None:
        return
    now = datetime.now(UTC)
    batch.status = BATCH_STATUS_DONE
    batch.completed_at = now
    batch.next_poll_at = None
    batch.updated_at = now
    session.add(batch)


async def _result_from_batch(
    session: AsyncSession,
    batch: LLMMatchBatch,
    *,
    requeued: bool,
) -> BatchMatchTickResult:
    rows = (
        await session.execute(
            select(LLMMatchBatchItem.status).where(LLMMatchBatchItem.batch_id == batch.id)
        )
    ).all()
    statuses = [row[0] for row in rows]
    return BatchMatchTickResult(
        imported=statuses.count(ITEM_STATUS_IMPORTED),
        retryable_failed=statuses.count(ITEM_STATUS_RETRYABLE_FAILED),
        terminal_failed=statuses.count(ITEM_STATUS_TERMINAL_FAILED),
        requeued=requeued,
    )
