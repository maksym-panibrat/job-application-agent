import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
import sqlalchemy as sa

from app.models.application import Application
from app.models.company import Company
from app.models.job import Job
from app.models.slug_fetch import SlugFetch
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue, WorkQueueStatus
from app.sources.greenhouse_board import GREENHOUSE_BOARDS_BASE
from app.worker.handlers import HANDLERS
from app.worker.handlers.fetch_slug import FetchSlugHandler


@pytest.fixture(autouse=True)
def disable_batch_match(monkeypatch):
    import app.config as cfg

    monkeypatch.setenv("BATCH_MATCH_ENABLED", "false")
    cfg._settings = None
    yield
    cfg._settings = None


async def _seed_interested_profile(db_session, slug: str) -> None:
    user = User(id=uuid.uuid4(), email=f"fetch-handler-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    company = Company(
        canonical_name=slug.title(),
        normalized_key=f"{slug}-{uuid.uuid4()}",
        provider_slugs={"greenhouse": slug},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    profile = UserProfile(
        user_id=user.id,
        target_company_ids=[company.id],
        search_active=True,
    )
    db_session.add(profile)
    db_session.add(SlugFetch(source="greenhouse", slug=slug))
    await db_session.commit()


def _greenhouse_fixture(slug: str, external_id: int = 9001) -> dict:
    return {
        "jobs": [
            {
                "id": external_id,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{external_id}",
                "updated_at": datetime.now(UTC).isoformat(),
                "content": "<p>job</p>",
            }
        ]
    }


def _fetch_row(slug: str) -> WorkQueue:
    return WorkQueue(
        id=1,
        job_type="fetch-slug",
        payload={"provider": "greenhouse", "slug": slug},
        status=WorkQueueStatus.IN_PROGRESS,
        attempts=1,
        claimed_by="w1",
    )


@pytest.mark.asyncio
async def test_fetch_slug_handler_upserts_jobs_and_enqueues_match_rows(db_session):
    slug = "airbnb"
    await _seed_interested_profile(db_session, slug)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/{slug}/jobs").mock(
            return_value=httpx.Response(200, json=_greenhouse_fixture(slug))
        )
        await FetchSlugHandler()(db_session, _fetch_row(slug))

    db_session.expire_all()
    jobs = (await db_session.execute(sa.select(Job))).scalars().all()
    apps = (await db_session.execute(sa.select(Application))).scalars().all()
    match_jobs = (
        await db_session.execute(
            sa.text(
                "SELECT payload->>'application_id', dedupe_key "
                "FROM work_queue WHERE job_type='match'"
            )
        )
    ).all()
    slug_row = (
        await db_session.execute(
            sa.select(SlugFetch).where(SlugFetch.source == "greenhouse", SlugFetch.slug == slug)
        )
    ).scalar_one()

    assert isinstance(HANDLERS["fetch-slug"], FetchSlugHandler)
    assert len(jobs) == 1
    assert len(apps) == 1
    assert apps[0].job_id == jobs[0].id
    assert match_jobs == [(str(apps[0].id), f"match:{apps[0].id}")]
    assert slug_row.consecutive_5xx_count == 0


@pytest.mark.asyncio
async def test_fetch_slug_handler_replay_is_idempotent(db_session):
    slug = "stripe"
    await _seed_interested_profile(db_session, slug)
    handler = FetchSlugHandler()
    row = _fetch_row(slug)

    with respx.mock:
        respx.get(f"{GREENHOUSE_BOARDS_BASE}/{slug}/jobs").mock(
            return_value=httpx.Response(200, json=_greenhouse_fixture(slug, external_id=42))
        )
        await handler(db_session, row)
        await handler(db_session, row)

    db_session.expire_all()
    job_count = (await db_session.execute(sa.select(sa.func.count()).select_from(Job))).scalar_one()
    app_count = (
        await db_session.execute(sa.select(sa.func.count()).select_from(Application))
    ).scalar_one()
    match_count = (
        await db_session.execute(
            sa.text("SELECT count(*) FROM work_queue WHERE job_type='match'")
        )
    ).scalar_one()
    slug_row = (
        await db_session.execute(
            sa.select(SlugFetch).where(SlugFetch.source == "greenhouse", SlugFetch.slug == slug)
        )
    ).scalar_one()

    assert job_count == 1
    assert app_count == 1
    assert match_count == 1
    assert slug_row.consecutive_5xx_count <= 1
