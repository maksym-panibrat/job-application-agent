import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.models.company import Company
from app.models.slug_fetch import SlugFetch
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.work_queue import WorkQueue

CRON_SECRET = "dev-cron-secret"


@pytest.mark.asyncio
async def test_cron_sync_enqueues_fetch_slug_work_rows(db_session):
    company = Company(
        canonical_name="Co",
        normalized_key=f"co-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "co"},
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    user = User(id=uuid.uuid4(), email=f"sync-cron-{uuid.uuid4()}@test.com")
    db_session.add(user)
    await db_session.commit()

    profile = UserProfile(
        user_id=user.id,
        email=user.email,
        search_active=True,
        target_company_ids=[company.id],
    )
    db_session.add(profile)
    db_session.add(
        SlugFetch(
            source="greenhouse",
            slug="co",
            last_fetched_at=datetime.now(UTC) - timedelta(hours=7),
        )
    )
    await db_session.commit()

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/cron/sync",
            headers={"X-Cron-Secret": CRON_SECRET},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["active_profiles"] == 1
    assert body["profiles_enqueued"] == 1
    assert body["pruned"] == 0
    assert body["enqueued"] == ["co"]

    rows = (
        (
            await db_session.execute(
                sa.select(WorkQueue).where(WorkQueue.job_type == "fetch-slug")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].dedupe_key == "fetch-slug:greenhouse:co"
    assert rows[0].payload == {"provider": "greenhouse", "slug": "co"}
