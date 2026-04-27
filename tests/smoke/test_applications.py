"""
Smoke tests: core job application workflow.

Calls real Adzuna + JSearch APIs and Claude for scoring and document generation.
Allow up to 5 min total — scoring is throttled to avoid rate limits, generation
runs two LLM calls (resume + cover letter) per application.
"""

import io

import pytest

from tests.smoke.conftest import poll_until

# Realistic resume so the matching agent produces scores above the 0.65 threshold.
_RESUME = """\
# Maksym Panibratenko
maksym@panibrat.com | github.com/mpanibrat

## Summary
Backend engineer with 6 years of Python experience building distributed systems,
REST APIs, and data pipelines. Currently senior IC at a fintech company.

## Experience

### Senior Software Engineer - FinCo (2021-present)
- Built microservices with FastAPI, Python 3.11, and PostgreSQL
- Designed async data pipelines using asyncio and Redis Streams
- Led migration of monolith to containerised services (Docker/K8s)
- Mentored two junior engineers; conducted architecture reviews

### Software Engineer - CloudStartup (2018-2021)
- Developed REST APIs in Django REST Framework serving 50k req/day
- Optimised slow PostgreSQL queries; reduced p99 latency by 40 percent
- Integrated third-party APIs (Stripe, Twilio, SendGrid)

## Skills
Python, FastAPI, Django, PostgreSQL, Redis, Docker, Kubernetes, AWS (ECS/RDS/S3),
asyncio, SQLAlchemy, Alembic, pytest, CI/CD (GitHub Actions), REST APIs, microservices
"""


@pytest.mark.asyncio
async def test_core_workflow(client):
    """
    Full pipeline:
      profile setup -> job sync (Adzuna + JSearch) -> LLM scoring
      -> document generation (resume + cover letter) -> review -> dismiss
    """
    # 0. Reset stale scoring state from previous runs
    clear = await client.delete("/api/test/applications")
    assert clear.status_code == 200

    # 1. Upload a realistic resume
    resume_resp = await client.post(
        "/api/profile/upload",
        files={"file": ("resume.txt", io.BytesIO(_RESUME.encode()), "text/plain")},
    )
    assert resume_resp.status_code == 200

    # 2. Set target roles
    patch = await client.patch(
        "/api/profile",
        json={"target_roles": ["Python Engineer", "Backend Engineer"], "remote_ok": True},
    )
    assert patch.status_code == 200

    # 3. Trigger sync — fetches from all configured sources, kicks off background scoring
    sync = await client.post("/api/jobs/sync")
    assert sync.status_code == 200
    sync_data = sync.json()
    assert "new_jobs" in sync_data
    assert "updated_jobs" in sync_data

    # Verify Adzuna is active; assert JSearch is also active (both sources required)
    sources = sync_data.get("sources", [])
    assert "adzuna" in sources, f"Adzuna not in active sources: {sources}"
    assert "jsearch" in sources, (
        f"JSearch not in active sources: {sources}. "
        "Set JSEARCH_API_KEY in .env to enable the second source."
    )

    # 4. Poll until scored applications appear (throttled scoring takes ~15-30s)
    apps = await poll_until(
        client,
        "/api/applications?status=pending_review",
        predicate=lambda data: len(data) > 0,
        timeout=150,
        interval=5,
    )
    assert len(apps) > 0, "No pending_review applications after sync + scoring"

    # 5. Verify scoring populated match fields
    app = apps[0]
    assert app["match_score"] is not None
    assert 0 <= app["match_score"] <= 1
    assert app["job"]["title"]
    assert app["job"]["company_name"]

    # 6. Trigger document generation for matched applications
    gen = await client.post("/api/test/generate")
    assert gen.status_code == 200
    assert gen.json()["triggered"] >= 1

    # 7. Poll until the first application has generation_status=ready
    app_id = app["id"]
    detail = await poll_until(
        client,
        f"/api/applications/{app_id}",
        predicate=lambda d: d.get("generation_status") == "ready",
        timeout=120,
        interval=5,
    )

    # 8. Verify both documents were generated
    doc_types = {d["doc_type"] for d in detail["documents"]}
    assert "tailored_resume" in doc_types, f"Missing tailored_resume, got: {doc_types}"
    assert "cover_letter" in doc_types, f"Missing cover_letter, got: {doc_types}"
    for doc in detail["documents"]:
        assert len(doc["content_md"]) > 100, f"{doc['doc_type']} content suspiciously short"

    # 9. Dismiss the application
    dismiss = await client.patch(f"/api/applications/{app_id}", json={"status": "dismissed"})
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"

    # 10. Dismissed application must not appear in pending_review
    pending_resp = await client.get("/api/applications?status=pending_review")
    assert pending_resp.status_code == 200
    assert app_id not in {a["id"] for a in pending_resp.json()}


@pytest.mark.asyncio
async def test_mark_applied(client, seeded_data):
    """Verify /mark-applied sets status=applied and records applied_at."""
    apps = seeded_data.get("applications", [])
    assert apps, "No seeded applications — seeded_data is empty"
    app_id = apps[0]["id"]

    # /submit must no longer exist (404 if no path matches, 405 if method mismatch)
    r_submit = await client.post(f"/api/applications/{app_id}/submit")
    assert r_submit.status_code in (404, 405), (
        f"Expected 404/405 for removed /submit, got {r_submit.status_code}"
    )

    # Mark as applied
    r = await client.post(f"/api/applications/{app_id}/mark-applied")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "applied"
    assert body["applied_at"] is not None

    # Idempotent — second call returns same applied_at
    r2 = await client.post(f"/api/applications/{app_id}/mark-applied")
    assert r2.status_code == 200
    assert r2.json()["applied_at"] == body["applied_at"]
