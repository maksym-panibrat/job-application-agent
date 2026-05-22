# Neon Egress Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce routine Neon network transfer from cron, polling, list endpoints, and repeated worker reads while preserving full raw job descriptions in storage.

**Architecture:** Add measurement first, then remove wide-row reads from hot paths, make sync/upsert work set-based and change-aware, and stop frontend polling when auth is no longer valid. Keep raw provider descriptions in `jobs.description_raw`; reduce transfer by narrowing queries and response payloads, not by truncating stored data.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async sessions, Alembic, Postgres/Neon, React, TanStack Query, Vitest, pytest.

---

## File Structure

- Create `scripts/neon_egress_diagnostics.sql`: reusable operator SQL for `pg_stat_statements` and table stats.
- Modify `docs/DEPLOYMENT.md`: runbook for enabling/resetting stats and reading transfer signals.
- Modify `app/services/slug_registry_service.py`: add set-based active-profile stale slug discovery.
- Modify `app/services/job_sync_service.py`: use the set-based helper for cron sync and keep manual profile sync stable.
- Modify `app/api/internal_cron.py`: keep `/internal/cron/sync` response shape stable.
- Modify `app/services/match_service.py`: add narrow application-list and worker-scoring projections.
- Modify `app/api/applications.py`: serialize list rows from the narrow projection and remove raw descriptions from detail by default.
- Modify `frontend/src/api/client.ts`: handle 401 globally and align application types with the new payload contract.
- Modify `frontend/src/lib/useSyncControl.ts`: add 401 stop behavior and live-state backoff.
- Modify `frontend/src/components/BudgetBanner.tsx`: replace unconditional interval polling with React Query caching.
- Modify `app/models/job.py`: add nullable `content_hash`.
- Create an Alembic migration adding `jobs.content_hash`.
- Modify `app/services/job_service.py`: compute content hashes and skip wide updates for unchanged postings.
- Modify `app/observability/queue_depth.py` and `app/config.py`: make queue-depth interval configurable.
- Create `tests/unit/test_match_service_queries.py`: compile-query guardrails for wide-column regressions.
- Update tests in `tests/integration/`, `tests/unit/`, and `frontend/src/` to lock the egress-sensitive behavior.

## Scope Notes

The production cron schedule appears to be owned outside this repository by the Hetzner/supercronic deployment. This plan still reduces this repo's cron work by making active-profile stale discovery set-based. The external schedule must also be changed from roughly every 15 minutes to every 6 hours in the deployment repo/runbook to satisfy the spec's production acceptance criterion.

## Task 1: Measurement Runbook

**Files:**
- Create: `scripts/neon_egress_diagnostics.sql`
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Add the diagnostic SQL script**

Create `scripts/neon_egress_diagnostics.sql` with:

```sql
-- Neon egress diagnostics.
-- Usage:
--   psql "$DATABASE_URL" -f scripts/neon_egress_diagnostics.sql
--
-- These reports rank query candidates by rows and call frequency. They are not
-- exact byte-level network-transfer reports.

\echo 'pg_stat_statements availability'
SELECT EXISTS (
  SELECT 1
  FROM pg_extension
  WHERE extname = 'pg_stat_statements'
) AS pg_stat_statements_installed;

\echo 'top total returned rows'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY rows DESC
LIMIT 20;

\echo 'top rows per call'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY avg_rows_per_call DESC NULLS LAST
LIMIT 20;

\echo 'most frequent queries'
SELECT query, calls, rows AS total_rows, rows / NULLIF(calls, 0) AS avg_rows_per_call
FROM pg_stat_statements
WHERE calls > 0
ORDER BY calls DESC
LIMIT 20;

\echo 'longest total execution time'
SELECT query, calls, rows AS total_rows,
       round(total_exec_time::numeric, 2) AS total_exec_time_ms
FROM pg_stat_statements
WHERE calls > 0
ORDER BY total_exec_time DESC
LIMIT 20;

\echo 'hot table stats'
SELECT relname,
       n_live_tup,
       n_dead_tup,
       seq_scan,
       seq_tup_read,
       idx_scan,
       idx_tup_fetch,
       n_tup_ins,
       n_tup_upd,
       n_tup_del,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
ORDER BY seq_tup_read + idx_tup_fetch DESC
LIMIT 30;
```

- [ ] **Step 2: Add the deployment runbook section**

Append this section to `docs/DEPLOYMENT.md`:

```markdown
## Neon Egress Measurement

Neon network transfer is data sent from Postgres through Neon's proxy to clients.
The database can be small at rest while still exhausting transfer allowance when
hot paths repeatedly fetch wide rows.

Before a measurement window:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT pg_stat_statements_reset();
```

After at least 24 hours of representative traffic:

```bash
psql "$DATABASE_URL" -f scripts/neon_egress_diagnostics.sql
```

Interpretation:

- high `rows` plus wide tables such as `jobs` means likely high transfer
- high `calls` means polling, cron, or auth loops may dominate even with small rows
- `pg_stat_statements` does not report exact bytes; compare rows, calls, and table stats before and after changes

Expected healthy production cadence:

- `/internal/cron/sync`: about 4/day
- `/internal/cron/generation-reconcile`: about 48/day
- `/internal/cron/maintenance`: about 1/day
- `/api/sync/status`: only while an authenticated user has live sync/match work
- `/api/status`: cached on the frontend, not a per-component minute loop
```

- [ ] **Step 3: Run a docs/script smoke check**

Run:

```bash
test -f scripts/neon_egress_diagnostics.sql
rg -n "Neon Egress Measurement|pg_stat_statements_reset|neon_egress_diagnostics" docs/DEPLOYMENT.md scripts/neon_egress_diagnostics.sql
```

Expected: all three search terms appear.

- [ ] **Step 4: Commit**

```bash
git add docs/DEPLOYMENT.md scripts/neon_egress_diagnostics.sql
git commit -m "docs: add Neon egress measurement runbook"
```

## Task 2: Set-Based Cron Stale Slug Discovery

**Files:**
- Modify: `app/services/slug_registry_service.py`
- Modify: `app/services/job_sync_service.py`
- Modify: `tests/integration/test_job_sync.py`
- Modify: `tests/integration/test_cron_sync_enqueues.py`

- [ ] **Step 1: Add failing service coverage for shared slug dedupe**

Add this test to `tests/integration/test_job_sync.py`:

```python
@pytest.mark.asyncio
async def test_sync_active_profiles_deduplicates_shared_provider_slugs(db_session):
    from datetime import timedelta

    from sqlmodel import col, select

    from app.models.company import Company
    from app.models.slug_fetch import SlugFetch
    from app.models.user import User
    from app.models.user_profile import UserProfile
    from app.models.work_queue import WorkQueue

    company = Company(
        canonical_name="Shared Co",
        normalized_key=f"shared-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "shared-co"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    users = [
        User(id=uuid.uuid4(), email=f"shared-{i}-{uuid.uuid4()}@test.com")
        for i in range(2)
    ]
    db_session.add_all(users)
    await db_session.commit()
    db_session.add_all(
        [
            UserProfile(
                user_id=users[0].id,
                email=users[0].email,
                search_active=True,
                target_company_ids=[company.id],
            ),
            UserProfile(
                user_id=users[1].id,
                email=users[1].email,
                search_active=True,
                target_company_ids=[company.id],
            ),
            SlugFetch(
                source="greenhouse",
                slug="shared-co",
                last_fetched_at=datetime.now(UTC) - timedelta(hours=7),
            ),
        ]
    )
    await db_session.commit()

    result = await job_sync_service.sync_active_profiles(db_session)

    assert result["active_profiles"] == 2
    assert result["profiles_enqueued"] == 2
    assert result["enqueued"] == ["shared-co"]
    rows = (
        (
            await db_session.execute(
                select(WorkQueue).where(WorkQueue.job_type == "fetch-slug")
            )
        )
        .scalars()
        .all()
    )
    assert [row.dedupe_key for row in rows] == ["fetch-slug:greenhouse:shared-co"]
    profiles = (
        (
            await db_session.execute(
                select(UserProfile).where(col(UserProfile.search_active).is_(True))
            )
        )
        .scalars()
        .all()
    )
    summaries = {profile.email: profile.last_sync_summary for profile in profiles}
    assert summaries[users[0].email]["queued_slugs"] == ["shared-co"]
    assert summaries[users[1].email]["queued_slugs"] == ["shared-co"]
```

Also add this test to ensure cron summaries stay profile-specific and invalid slug pruning is preserved:

```python
@pytest.mark.asyncio
async def test_sync_active_profiles_keeps_profile_specific_summaries_and_pruning(db_session):
    from datetime import timedelta

    from sqlmodel import col, select

    from app.models.company import Company
    from app.models.slug_fetch import SlugFetch
    from app.models.user import User
    from app.models.user_profile import UserProfile

    stale_company = Company(
        canonical_name="Stale Co",
        normalized_key=f"stale-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "stale-co"},
        resolved_at=datetime.now(UTC),
    )
    fresh_company = Company(
        canonical_name="Fresh Co",
        normalized_key=f"fresh-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "fresh-co"},
        resolved_at=datetime.now(UTC),
    )
    invalid_company = Company(
        canonical_name="Invalid Co",
        normalized_key=f"invalid-{uuid.uuid4()}",
        provider_slugs={"greenhouse": "invalid-co"},
        resolved_at=datetime.now(UTC),
    )
    db_session.add_all([stale_company, fresh_company, invalid_company])
    await db_session.commit()
    await db_session.refresh(stale_company)
    await db_session.refresh(fresh_company)
    await db_session.refresh(invalid_company)

    stale_user = User(id=uuid.uuid4(), email=f"stale-{uuid.uuid4()}@test.com")
    fresh_user = User(id=uuid.uuid4(), email=f"fresh-{uuid.uuid4()}@test.com")
    db_session.add_all([stale_user, fresh_user])
    await db_session.commit()

    db_session.add_all(
        [
            UserProfile(
                user_id=stale_user.id,
                email=stale_user.email,
                search_active=True,
                target_company_ids=[stale_company.id],
            ),
            UserProfile(
                user_id=fresh_user.id,
                email=fresh_user.email,
                search_active=True,
                target_company_ids=[fresh_company.id, invalid_company.id],
            ),
            SlugFetch(
                source="greenhouse",
                slug="stale-co",
                last_fetched_at=datetime.now(UTC) - timedelta(hours=7),
            ),
            SlugFetch(
                source="greenhouse",
                slug="fresh-co",
                last_fetched_at=datetime.now(UTC),
            ),
            SlugFetch(source="greenhouse", slug="invalid-co", is_invalid=True),
        ]
    )
    await db_session.commit()

    result = await job_sync_service.sync_active_profiles(db_session)

    assert result["enqueued"] == ["stale-co"]
    assert result["pruned"] == 1
    refreshed_profiles = (
        (
            await db_session.execute(
                select(UserProfile).where(col(UserProfile.search_active).is_(True))
            )
        )
        .scalars()
        .all()
    )
    summaries = {profile.email: profile.last_sync_summary for profile in refreshed_profiles}
    assert summaries[stale_user.email]["queued_slugs"] == ["stale-co"]
    assert summaries[stale_user.email]["pruned_slugs"] == []
    assert summaries[fresh_user.email]["queued_slugs"] == []
    assert summaries[fresh_user.email]["pruned_slugs"] == ["greenhouse:invalid-co"]
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/integration/test_job_sync.py::test_sync_active_profiles_deduplicates_shared_provider_slugs tests/integration/test_job_sync.py::test_sync_active_profiles_keeps_profile_specific_summaries_and_pruning -v
```

Expected before implementation: FAIL because the current cron sync loops profile-by-profile and reports duplicate `enqueued` values or repeats stale-slug lookup work.

- [ ] **Step 3: Add set-based active-profile stale query**

In `app/services/slug_registry_service.py`, add:

```python
async def list_stale_for_active_profiles(
    session: AsyncSession,
    *,
    ttl_hours: int = 6,
) -> list[tuple[uuid.UUID, str, str]]:
    """Return stale provider slugs by active profile without per-slug lookups."""
    result = await session.execute(
        sa.text(
            """
            WITH active_companies AS (
              SELECT id AS profile_id, unnest(target_company_ids) AS company_id
              FROM user_profiles
              WHERE search_active IS TRUE
                AND target_company_ids IS NOT NULL
            ),
            provider_slugs AS (
              SELECT DISTINCT
                     ac.profile_id,
                     kv.key::text AS source,
                     kv.value::text AS slug
              FROM active_companies ac
              JOIN companies c ON c.id = ac.company_id
              CROSS JOIN LATERAL jsonb_each_text(c.provider_slugs) AS kv(key, value)
              WHERE c.unfollowable IS FALSE
                AND kv.value IS NOT NULL
                AND kv.value <> ''
            )
            SELECT ps.profile_id, ps.source, ps.slug
            FROM provider_slugs ps
            LEFT JOIN slug_fetches sf
              ON sf.source = ps.source
             AND sf.slug = ps.slug
            WHERE COALESCE(sf.is_invalid, false) IS FALSE
              AND (
                sf.source IS NULL
                OR sf.last_fetched_at IS NULL
                OR sf.last_fetched_at < now() - make_interval(hours => :ttl_hours)
              )
            ORDER BY ps.profile_id, ps.source, ps.slug
            """
        ),
        {"ttl_hours": ttl_hours},
    )
    return [(row[0], row[1], row[2]) for row in result.all()]
```

Also add these imports at the top of the file:

```python
import uuid

import sqlalchemy as sa
```

- [ ] **Step 4: Use the set-based helper in cron sync**

In `app/services/job_sync_service.py`, add `import uuid`, then rewrite `sync_active_profiles()` so it:

```python
async def sync_active_profiles(session: AsyncSession) -> dict:
    """Cron/scheduler sweep: enqueue distinct stale provider slugs for active profiles."""
    active_profiles = (
        (
            await session.execute(
                select(UserProfile).where(col(UserProfile.search_active).is_(True))
            )
        )
        .scalars()
        .all()
    )

    pruned_by_profile: dict[uuid.UUID, list[str]] = {}
    for profile in active_profiles:
        pruned_by_profile[profile.id] = await _prune_invalid_provider_slugs(profile, session)

    stale_by_profile = await slug_registry_service.list_stale_for_active_profiles(
        session,
        ttl_hours=6,
    )

    pairs_by_profile: dict[uuid.UUID, list[tuple[str, str]]] = {}
    distinct_pairs: dict[tuple[str, str], str] = {}
    for profile_id, provider, slug in stale_by_profile:
        pairs_by_profile.setdefault(profile_id, []).append((provider, slug))
        distinct_pairs.setdefault((provider, slug), slug)

    enqueued: list[str] = []
    enqueued_pairs: set[tuple[str, str]] = set()
    for (provider, slug), display_slug in distinct_pairs.items():
        row_id = await enqueue(
            session,
            job_type="fetch-slug",
            payload=FetchSlugPayload(provider=provider, slug=slug).model_dump(),
            dedupe_key=f"fetch-slug:{provider}:{slug}",
        )
        if row_id is not None:
            enqueued.append(display_slug)
            enqueued_pairs.add((provider, slug))

    now = datetime.now(UTC)
    profiles_enqueued = 0
    for profile in active_profiles:
        profile_slugs = [
            slug
            for provider, slug in pairs_by_profile.get(profile.id, [])
            if (provider, slug) in enqueued_pairs
        ]
        profile.last_sync_requested_at = now
        profile.last_sync_summary = {
            "queued_slugs": profile_slugs,
            "matched_now": 0,
            "pruned_slugs": pruned_by_profile.get(profile.id, []),
        }
        if profile_slugs:
            profiles_enqueued += 1
        else:
            profile.last_sync_completed_at = now
        session.add(profile)

    await session.commit()
    return {
        "enqueued": enqueued,
        "pruned": sum(len(values) for values in pruned_by_profile.values()),
        "active_profiles": len(active_profiles),
        "profiles_enqueued": profiles_enqueued,
    }
```

Keep `_prune_invalid_provider_slugs()` and `prune_and_enqueue()` unchanged for manual per-profile sync in this task. The cron path still prunes invalid slugs per profile to preserve current behavior, but stale-slug discovery no longer performs per-profile, per-provider-slug freshness reads.

- [ ] **Step 5: Run targeted sync tests**

Run:

```bash
uv run pytest tests/integration/test_job_sync.py::test_sync_active_profiles_uses_shared_enqueue_contract tests/integration/test_job_sync.py::test_sync_active_profiles_deduplicates_shared_provider_slugs tests/integration/test_job_sync.py::test_sync_active_profiles_keeps_profile_specific_summaries_and_pruning tests/integration/test_cron_sync_enqueues.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/slug_registry_service.py app/services/job_sync_service.py tests/integration/test_job_sync.py tests/integration/test_cron_sync_enqueues.py
git commit -m "refactor(sync): dedupe stale slug cron discovery"
```

## Task 3: Narrow Application List And Detail Payloads

**Files:**
- Modify: `app/services/match_service.py`
- Modify: `app/api/applications.py`
- Modify: `frontend/src/api/client.ts`
- Create: `tests/unit/test_match_service_queries.py`
- Modify: `tests/integration/test_applications_api_summary.py`
- Modify: `tests/integration/test_jobs_endpoint.py`
- Modify: `frontend/src/pages/ApplicationReview.test.tsx`
- Modify: `frontend/src/components/feed/MatchCard.test.tsx`

- [ ] **Step 1: Add failing backend tests for list/detail description behavior**

Create `tests/unit/test_match_service_queries.py` with a compile-time query guard:

```python
import uuid


def test_application_list_query_does_not_select_job_descriptions():
    from app.services.match_service import build_application_list_query

    query = build_application_list_query(uuid.uuid4(), status=None, min_score=None)
    compiled = str(query.compile(compile_kwargs={"literal_binds": False})).lower()

    assert "jobs.description_raw" not in compiled
    assert "jobs.description" not in compiled
```

In `tests/integration/test_applications_api_summary.py`, replace
`test_detail_endpoint_exposes_description` with:

```python
@pytest.mark.asyncio
async def test_application_detail_omits_raw_description_by_default(
    db_session, auth_headers, seeded_user
):
    from httpx import ASGITransport, AsyncClient

    from app.main import app as fastapi_app
    from app.models.application import Application
    from app.models.job import Job

    _, profile = seeded_user
    job = Job(
        source="greenhouse",
        external_id="detail-raw-1",
        title="Detail Row",
        company_name="Detail Co",
        description_raw="<p>raw html</p>",
        description="clean markdown",
        apply_url="https://example.com/apply",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    app = Application(job_id=job.id, profile_id=profile.id, match_score=0.9)
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/applications/{app.id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["description"] == "clean markdown"
    assert "description_raw" not in body["job"]
```

- [ ] **Step 2: Run the failing backend tests**

Run:

```bash
uv run pytest tests/unit/test_match_service_queries.py::test_application_list_query_does_not_select_job_descriptions tests/integration/test_applications_api_summary.py::test_application_detail_omits_raw_description_by_default -v
```

Expected before implementation: FAIL because `build_application_list_query` does not exist and the detail response still includes `description_raw`.

- [ ] **Step 3: Add a narrow list projection**

In `app/services/match_service.py`, add a typed alias and query builder near `list_applications()`:

```python
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


def build_application_list_query(
    profile_id: uuid.UUID,
    *,
    status: str | None,
    min_score: float | None,
):
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
    )
    if status:
        q = q.where(Application.status == status)
        if status == "pending_review":
            q = q.where(col(Application.match_score).is_not(None))
    if min_score is not None:
        q = q.where(Application.match_score >= min_score)
    return q
```

Then rewrite `list_applications()` to use the builder and return projected rows:

```python
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
```

The builder must not include:

```python
select(Application, Job)
select(Job)
Job.description_raw
Job.description
```

The final selected-column list must remain:

```python
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
```

Return `list(result.tuples().all())`.

- [ ] **Step 4: Update list serialization**

In `app/api/applications.py`, update `list_applications()` to unpack the projected tuple and build the same card payload without `description_raw` or `description`:

```python
for (
    app_id,
    app_status,
    generation_status,
    match_score,
    match_summary,
    match_rationale,
    match_strengths,
    match_gaps,
    created_at,
    job_id,
    title,
    company_name,
    location,
    workplace_type,
    salary,
    contract_type,
    apply_url,
    posted_at,
) in rows:
    result.append(
        {
            "id": str(app_id),
            "status": app_status,
            "generation_status": generation_status,
            "match_score": match_score,
            "match_summary": match_summary,
            "match_rationale": match_rationale,
            "match_strengths": match_strengths,
            "match_gaps": match_gaps,
            "created_at": created_at,
            "job": {
                "id": str(job_id),
                "title": title,
                "company_name": company_name,
                "location": location,
                "workplace_type": workplace_type,
                "salary": salary,
                "contract_type": contract_type,
                "apply_url": apply_url,
                "posted_at": posted_at,
            },
        }
    )
```

In `get_application()`, remove `"description_raw": job.description_raw` from the job object.

- [ ] **Step 5: Update frontend types**

In `frontend/src/api/client.ts`, change `Job` to:

```ts
export interface Job {
  id: string
  title: string
  company_name: string
  location: string | null
  workplace_type: string | null
  salary: string | null
  contract_type: string | null
  description?: string | null
  apply_url: string
  posted_at: string | null
}
```

Remove `description_raw` from the public client type.

- [ ] **Step 6: Update frontend fixtures**

Update tests that include mock jobs to remove `description_raw`. For detail-page fixtures, keep `description`. For feed/card fixtures, omit both description fields.

- [ ] **Step 7: Run targeted backend/frontend tests**

Run:

```bash
uv run pytest tests/integration/test_applications_api_summary.py tests/integration/test_jobs_endpoint.py -v
uv run pytest tests/unit/test_match_service_queries.py -v
cd frontend && npm test -- ApplicationReview.test.tsx MatchCard.test.tsx client.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/match_service.py app/api/applications.py frontend/src/api/client.ts tests/unit/test_match_service_queries.py tests/integration/test_applications_api_summary.py tests/integration/test_jobs_endpoint.py frontend/src/pages/ApplicationReview.test.tsx frontend/src/components/feed/MatchCard.test.tsx
git commit -m "refactor(applications): avoid wide job payloads"
```

## Task 4: Change-Aware Job Upserts

**Files:**
- Modify: `app/models/job.py`
- Create: `alembic/versions/d8f2c4a9b1e7_add_jobs_content_hash.py`
- Modify: `app/services/job_service.py`
- Modify: `tests/integration/test_job_sync.py`

- [ ] **Step 1: Add failing upsert tests**

Add these tests to `tests/integration/test_job_sync.py`:

```python
@pytest.mark.asyncio
async def test_upsert_job_unchanged_payload_avoids_wide_select_and_update(db_session):
    from sqlalchemy import event

    data = make_job_data(external_id="stable-1", title="Stable Engineer")
    first, created = await upsert_job(data, "greenhouse", db_session)
    assert created is True

    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def capture_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        second, created = await upsert_job(data, "greenhouse", db_session)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    selects = "\n".join(stmt for stmt in statements if stmt.lstrip().startswith("select"))
    updates = "\n".join(stmt for stmt in statements if stmt.lstrip().startswith("update"))

    assert created is False
    assert second.id == first.id
    assert "description_raw" not in selects
    assert "jobs.description," not in selects
    assert "description_raw" not in updates
    assert "description =" not in updates

    refreshed = await db_session.get(Job, first.id)
    assert refreshed.description_raw == data.description_raw
    assert refreshed.description and "Python engineer" in refreshed.description


@pytest.mark.asyncio
async def test_upsert_job_populates_content_hash(db_session):
    data = make_job_data(external_id="stable-hash-1", title="Stable Engineer")
    job, created = await upsert_job(data, "greenhouse", db_session)

    assert created is True
    assert job.content_hash is not None
    assert len(job.content_hash) == 64


@pytest.mark.asyncio
async def test_upsert_job_changes_content_hash_when_description_changes(db_session):
    data = make_job_data(external_id="stable-2", title="Stable Engineer")
    first, _ = await upsert_job(data, "greenhouse", db_session)
    first_hash = first.content_hash

    changed = make_job_data(external_id="stable-2", title="Stable Engineer")
    changed.description_raw = "A different role description."
    second, created = await upsert_job(changed, "greenhouse", db_session)

    assert created is False
    assert second.id == first.id
    assert second.content_hash != first_hash
    assert second.description_raw == "A different role description."
    assert "different role" in (second.description or "")
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/integration/test_job_sync.py::test_upsert_job_unchanged_payload_avoids_wide_select_and_update tests/integration/test_job_sync.py::test_upsert_job_populates_content_hash tests/integration/test_job_sync.py::test_upsert_job_changes_content_hash_when_description_changes -v
```

Expected before implementation: FAIL because `Job` has no `content_hash` and the unchanged existing-row path currently loads and rewrites full description fields.

- [ ] **Step 3: Add the model column**

In `app/models/job.py`, add after `description`:

```python
    content_hash: str | None = Field(default=None, index=True)
```

- [ ] **Step 4: Add Alembic migration**

Create `alembic/versions/d8f2c4a9b1e7_add_jobs_content_hash.py` with:

```python
"""add jobs content hash

Revision ID: d8f2c4a9b1e7
Revises: 5a6b7c8d9e0f
Create Date: 2026-05-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f2c4a9b1e7"
down_revision: str | None = "5a6b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("content_hash", sa.String(), nullable=True))
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_column("jobs", "content_hash")
```

Keep `down_revision` aligned with the current repository head shown above.

- [ ] **Step 5: Add hash helper and narrow unchanged path**

In `app/services/job_service.py`, add imports:

```python
import hashlib
import json

from sqlalchemy import update
```

Add helper:

```python
def compute_job_content_hash(job_data: JobData) -> str:
    payload = {
        "title": job_data.title,
        "company_name": job_data.company_name,
        "location": job_data.location,
        "workplace_type": job_data.workplace_type,
        "description_raw": job_data.description_raw,
        "salary": job_data.salary,
        "contract_type": job_data.contract_type,
        "apply_url": job_data.apply_url,
        "posted_at": job_data.posted_at.isoformat() if job_data.posted_at else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

In `upsert_job()`, compute `content_hash = compute_job_content_hash(job_data)` before the existing-row branch. Replace the current existing-row lookup with a narrow lookup:

```python
existing_row = (
    await session.execute(
        select(
            Job.id,
            Job.content_hash,
            Job.company_id,
            Job.company_name,
            Job.source,
        ).where(
            Job.source == source,
            Job.external_id == job_data.external_id,
        )
    )
).one_or_none()

company_id = await _resolve_company_id(source, slug, session)

if existing_row is not None:
    job_id, existing_hash, existing_company_id, existing_company_name, existing_source = existing_row
    now = datetime.now(UTC)
    values = {
        "is_active": True,
        "fetched_at": now,
    }
    resolved_company_id = existing_company_id
    if company_id is not None and existing_company_id != company_id:
        values["company_id"] = company_id
        resolved_company_id = company_id

    content_changed = existing_hash != content_hash
    if content_changed:
        cleaned = clean_html_to_markdown(job_data.description_raw)
        values.update(
            {
                "title": job_data.title,
                "company_name": job_data.company_name,
                "description_raw": job_data.description_raw,
                "description": cleaned,
                "salary": job_data.salary,
                "contract_type": job_data.contract_type,
                "apply_url": job_data.apply_url,
                "location": job_data.location,
                "workplace_type": job_data.workplace_type,
                "posted_at": job_data.posted_at,
                "content_hash": content_hash,
            }
        )
    await session.execute(update(Job).where(Job.id == job_id).values(**values))
    await session.commit()
    return (
        Job(
            id=job_id,
            source=existing_source,
            external_id=job_data.external_id,
            title=job_data.title if content_changed else job_data.title,
            company_name=job_data.company_name if content_changed else existing_company_name,
            company_id=resolved_company_id,
            location=job_data.location,
            workplace_type=job_data.workplace_type,
            description_raw=job_data.description_raw if content_changed else None,
            description=cleaned if content_changed else None,
            salary=job_data.salary,
            contract_type=job_data.contract_type,
            apply_url=job_data.apply_url,
            posted_at=job_data.posted_at,
            content_hash=content_hash,
        ),
        False,
    )
```

For new rows, set `content_hash=content_hash`. Keep the new-row `session.refresh(job)` because new provider postings must return a normal model object and this path is not the repeated unchanged-posting hot path.

- [ ] **Step 6: Run targeted upsert tests**

Run:

```bash
uv run pytest tests/integration/test_job_sync.py::test_upsert_job_creates_new tests/integration/test_job_sync.py::test_upsert_job_updates_existing tests/integration/test_job_sync.py::test_upsert_job_unchanged_payload_avoids_wide_select_and_update tests/integration/test_job_sync.py::test_upsert_job_populates_content_hash tests/integration/test_job_sync.py::test_upsert_job_changes_content_hash_when_description_changes tests/integration/test_job_sync.py::test_upsert_job_preserves_description_beyond_prompt_cap -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/models/job.py app/services/job_service.py alembic/versions/*_add_jobs_content_hash.py tests/integration/test_job_sync.py
git commit -m "feat(jobs): skip wide updates for unchanged postings"
```

## Task 5: Frontend Auth-Aware Polling And Status Caching

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/context/AuthContext.tsx`
- Modify: `frontend/src/lib/useSyncControl.ts`
- Modify: `frontend/src/components/BudgetBanner.tsx`
- Modify: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/components/AppShell.test.tsx`
- Modify: `frontend/src/components/BudgetBanner.test.tsx`

- [ ] **Step 1: Add failing API client test for 401 token clearing**

In `frontend/src/api/client.test.ts`, add:

```ts
it('clears the access token on 401', async () => {
  sessionStorage.setItem('access_token', 'expired-token')
  mockFetch(401, { detail: 'Invalid token' })

  await expect(api.getMe()).rejects.toThrow()

  expect(sessionStorage.getItem('access_token')).toBeNull()
})

it('clears the access token on 401 from direct fetch wrappers', async () => {
  sessionStorage.setItem('access_token', 'expired-token')
  mockFetch(401, { detail: 'Invalid token' })

  await expect(api.resolveCompany('Acme')).rejects.toThrow()

  expect(sessionStorage.getItem('access_token')).toBeNull()
})
```

- [ ] **Step 2: Add failing polling backoff/401 tests**

In `frontend/src/components/AppShell.test.tsx`, add one test that returns 401 from `/api/sync/status` and asserts no repeated calls after a short wait:

```ts
it('stops sync-status polling after a 401', async () => {
  let statusCalls = 0
  server.use(
    http.get('/api/sync/status', () => {
      statusCalls += 1
      return HttpResponse.json({ detail: 'Invalid token' }, { status: 401 })
    }),
  )
  renderShell()

  await waitFor(() => expect(statusCalls).toBe(1))
  await new Promise((r) => setTimeout(r, 3_500))
  expect(statusCalls).toBe(1)
}, 6_000)
```

- [ ] **Step 3: Add failing BudgetBanner request-rate test**

In `frontend/src/components/BudgetBanner.test.tsx`, add imports:

```ts
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
```

Add a render helper:

```ts
function renderBudgetBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <BudgetBanner />
    </QueryClientProvider>
  )
}
```

Replace existing `render(<BudgetBanner />)` calls in this file with
`renderBudgetBanner()`, then add:

```ts
it('does not use a component-local minute interval', async () => {
  vi.useFakeTimers()
  let calls = 0
  server.use(
    http.get('/api/status', () => {
      calls += 1
      return HttpResponse.json({ budget_exhausted: false, resumes_at: null })
    }),
  )
  renderBudgetBanner()
  await waitFor(() => expect(calls).toBe(1))

  await vi.advanceTimersByTimeAsync(60_000)
  expect(calls).toBe(1)
  vi.useRealTimers()
})
```

- [ ] **Step 4: Run failing frontend tests**

Run:

```bash
cd frontend && npm test -- client.test.ts AppShell.test.tsx BudgetBanner.test.tsx
```

Expected before implementation: FAIL on the new 401 clearing and BudgetBanner interval assertions.

- [ ] **Step 5: Clear token on 401 in API client**

In `frontend/src/api/client.ts`, add a shared 401 helper above `apiFetch()`:

```ts
function clearAuthOnUnauthorized(status: number) {
  if (status !== 401) return
  sessionStorage.removeItem('access_token')
  window.dispatchEvent(new CustomEvent('auth:token-expired'))
}
```

Update `apiFetch()` after receiving `res`:

```ts
  clearAuthOnUnauthorized(res.status)
```

Keep the existing error parsing below it, but make the thrown error include the
HTTP status so polling hooks can branch on it:

```ts
    throw new Error(detail ? `${res.status}: ${detail}` : `${res.status}: ${text}`)
```

Also call the same helper in direct `fetch` wrappers before their `if (!resp.ok)` or `if (!r.ok)` blocks:

```ts
    clearAuthOnUnauthorized(r.status)
```

and:

```ts
    clearAuthOnUnauthorized(resp.status)
```

This must cover `uploadResume()` and `resolveCompany()`, because both currently bypass `apiFetch()`.

- [ ] **Step 6: Listen for token expiry in AuthProvider**

In `frontend/src/context/AuthContext.tsx`, add an effect:

```ts
  useEffect(() => {
    const onExpired = () => {
      setToken(null)
      setUser(null)
    }
    window.addEventListener('auth:token-expired', onExpired)
    return () => window.removeEventListener('auth:token-expired', onExpired)
  }, [])
```

- [ ] **Step 7: Stop sync polling after 401 and add backoff**

In `frontend/src/lib/useSyncControl.ts`, replace the fixed live timeout with a counter:

```ts
    let livePolls = 0
```

Inside `poll()`:

```ts
        if (body.state !== 'idle') {
          livePolls += 1
          const interval = Math.min(POLL_MS * Math.max(1, livePolls), 30_000)
          timeoutId = setTimeout(poll, interval)
        }
```

In the catch block, stop scheduling future sync-status polls on 401:

```ts
        if ((err as Error)?.message?.includes('401')) {
          return
        }
```

- [ ] **Step 8: Convert BudgetBanner to React Query**

In `frontend/src/components/BudgetBanner.tsx`, replace local state/effect with:

```tsx
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function BudgetBanner() {
  const { data: status } = useQuery({
    queryKey: ['app-status'],
    queryFn: api.getStatus,
    staleTime: 10 * 60_000,
    refetchInterval: false,
  })

  if (!status?.budget_exhausted) return null

  const resumes = status.resumes_at
    ? new Date(status.resumes_at).toLocaleDateString(undefined, { month: 'long', day: 'numeric' })
    : 'next month'

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-center text-sm text-amber-800">
      AI features paused until {resumes} - job collection continues.
    </div>
  )
}
```

- [ ] **Step 9: Run targeted frontend tests**

Run:

```bash
cd frontend && npm test -- client.test.ts AppShell.test.tsx BudgetBanner.test.tsx
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/context/AuthContext.tsx frontend/src/lib/useSyncControl.ts frontend/src/components/BudgetBanner.tsx frontend/src/api/client.test.ts frontend/src/components/AppShell.test.tsx frontend/src/components/BudgetBanner.test.tsx
git commit -m "fix(frontend): stop stale auth polling"
```

## Task 6: Worker Scoring Narrow Reads

**Files:**
- Modify: `app/services/match_service.py`
- Modify: `tests/unit/test_match_service_queries.py`
- Modify: `tests/integration/test_match_scoring.py`

- [ ] **Step 1: Add failing query guard for score candidates**

Add this test to `tests/unit/test_match_service_queries.py`:

```python
def test_score_candidate_query_selects_only_scoring_columns():
    from app.services.match_service import build_score_candidate_query

    query = build_score_candidate_query(
        profile_id=uuid.uuid4(),
        company_ids=[uuid.uuid4()],
        matched_ids=set(),
        limit=20,
    )
    compiled = str(query.compile(compile_kwargs={"literal_binds": False})).lower()
    selected = compiled.split(" from ", 1)[0]

    assert "select jobs.id" in compiled
    assert "jobs.description_raw" in selected
    assert "jobs.description," in selected
    assert "jobs.created_at" not in compiled
    assert "jobs.updated_at" not in compiled
```

Expected before implementation: FAIL because `build_score_candidate_query` does not exist.

- [ ] **Step 2: Add scoring row alias and query builder**

In `app/services/match_service.py`, add near `ApplicationListRow`:

```python
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


def build_score_candidate_query(
    *,
    profile_id: uuid.UUID,
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
```

This query intentionally selects `description` and `description_raw` because scoring needs one description string, but it must not select the full `Job` ORM row.

- [ ] **Step 3: Use narrow score candidates in `score_and_match()`**

Replace the `candidates_q = select(Job)` branch in `score_and_match()` with:

```python
candidates_q = build_score_candidate_query(
    profile_id=profile.id,
    company_ids=company_ids,
    matched_ids=matched_ids,
    limit=settings.matching_jobs_per_batch,
)
candidate_rows: list[JobScoreRow] = list((await session.execute(candidates_q)).tuples().all())
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
```

Keep the existing `jobs` argument behavior unchanged. Tests that pass explicit `jobs=[job]` should continue to exercise deterministic policy and scoring logic without using the query builder.

- [ ] **Step 4: Run scoring tests**

Run:

```bash
uv run pytest tests/unit/test_match_service_queries.py tests/integration/test_match_scoring.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/match_service.py tests/unit/test_match_service_queries.py tests/integration/test_match_scoring.py
git commit -m "refactor(match): narrow worker scoring reads"
```

## Task 7: Queue Depth Interval Configuration

**Files:**
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Add failing config test**

In `tests/unit/test_config.py`, add:

```python
def test_queue_depth_emit_interval_can_be_configured(monkeypatch):
    from app import config

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    monkeypatch.setenv("QUEUE_DEPTH_EMIT_INTERVAL_S", "17")
    config._settings = None

    try:
        settings = config.get_settings()
        assert settings.queue_depth_emit_interval_s == 17
    finally:
        config._settings = None
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/unit/test_config.py::test_queue_depth_emit_interval_can_be_configured -v
```

Expected before implementation: FAIL because `Settings` has no `queue_depth_emit_interval_s`.

- [ ] **Step 3: Add setting and wire it**

In `app/config.py`, add:

```python
    queue_depth_emit_interval_s: int = 60
```

In `app/main.py`, change:

```python
depth_task = asyncio.create_task(
    _emit_queue_depth_forever(
        get_session_factory(),
        interval_s=settings.queue_depth_emit_interval_s,
    ),
    name="queue-depth-emitter",
)
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/integration/test_queue_depth_emitter.py tests/unit/test_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/main.py tests/unit/test_config.py
git commit -m "chore(observability): configure queue depth interval"
```

## Task 8: External Cron Schedule Update

**Files:**
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Document required external schedule change**

In `docs/DEPLOYMENT.md`, under the cron/scheduler section, add:

```markdown
### Cron Cadence

The job-search API expects these production trigger cadences:

| Endpoint | Cadence | Reason |
| --- | --- | --- |
| `POST /internal/cron/sync` | every 6 hours | provider slug freshness TTL is 6 hours |
| `POST /internal/cron/generation-reconcile` | every 30 minutes | repairs stuck cover-letter generation requests |
| `POST /internal/cron/maintenance` | daily | stale-job marking and retention cleanup |

Do not run `/internal/cron/sync` every 15 minutes. That cadence repeatedly scans
active profiles, companies, and slug freshness while producing no new work
inside the 6-hour TTL.
```

- [ ] **Step 2: Run docs check**

Run:

```bash
rg -n "Cron Cadence|every 6 hours|Do not run `/internal/cron/sync` every 15 minutes" docs/DEPLOYMENT.md
```

Expected: all phrases appear.

- [ ] **Step 3: Commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: define production cron cadence"
```

## Task 9: Final Verification And Measurement Prep

**Files:**
- Modify: none expected unless earlier tasks found gaps.

- [ ] **Step 1: Run backend focused suite**

Run:

```bash
uv run pytest \
  tests/integration/test_job_sync.py \
  tests/integration/test_cron_sync_enqueues.py \
  tests/integration/test_sync_status_endpoint.py \
  tests/integration/test_applications_api_summary.py \
  tests/integration/test_jobs_endpoint.py \
  tests/integration/test_queue_depth_emitter.py \
  tests/integration/test_match_scoring.py \
  -v
uv run pytest tests/unit/test_match_service_queries.py -v
```

Expected: PASS.

- [ ] **Step 2: Run frontend focused suite**

Run:

```bash
cd frontend && npm test -- client.test.ts AppShell.test.tsx BudgetBanner.test.tsx ApplicationReview.test.tsx MatchCard.test.tsx
```

Expected: PASS.

- [ ] **Step 3: Run broader unit/integration gates if time allows**

Run:

```bash
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v
cd frontend && npm run typecheck
cd frontend && npm test
```

Expected: PASS. If any unrelated existing failure appears, capture the exact test and error in the handoff instead of hiding it.

- [ ] **Step 4: Prepare production measurement commands**

Do not run production SQL from the implementation session unless explicitly approved. Prepare the commands for the deploy operator:

```bash
psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'
psql "$DATABASE_URL" -c 'SELECT pg_stat_statements_reset();'
psql "$DATABASE_URL" -f scripts/neon_egress_diagnostics.sql
```

Expected: the first two commands run at the start of the window, and the diagnostic script runs after at least 24 hours.

- [ ] **Step 5: Commit any verification-only documentation adjustment**

Only if Step 3 or Step 4 required doc edits:

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: clarify Neon egress verification"
```

## Self-Review Checklist

- Spec coverage:
  - Measurement: Task 1 and Task 9.
  - Cron cadence and set-based stale discovery: Task 2 and Task 8.
  - Narrow list/detail payloads: Task 3.
  - Change-aware upserts and content hash: Task 4.
  - Frontend polling and auth expiry: Task 5.
  - Worker scoring reads: Task 6.
  - Queue depth bounds: Task 7.
  - Operational verification: Task 9.
- No storage truncation is proposed; `description_raw` remains archival.
- The only deliberate API contract change is removing default `description_raw` from application detail responses.
- External production schedule ownership is called out explicitly because it is not represented by a cron file in this repository.
