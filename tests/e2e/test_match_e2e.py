"""
E2E tests for the full sync → score → list → detail flow.

Tests verify that:
- Match fields (score, rationale, strengths, gaps) are present in API responses
- Below-threshold jobs are excluded from pending_review list
- Matches are ordered by score desc

Background tasks run synchronously under httpx ASGITransport,
so scoring completes before POST /api/jobs/sync returns.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.base import JobData


def _make_job_data(idx: int = 0, title: str | None = None) -> JobData:
    return JobData(
        external_id=f"match-e2e-{idx:03d}",
        title=title or f"Software Engineer #{idx}",
        company_name="TechCorp",
        location="Remote",
        apply_url=f"https://example.com/jobs/{idx}",
        description_md="We need a great engineer to join our team.",
    )


def _mock_source(name: str, jobs: list[JobData]) -> MagicMock:
    source = MagicMock()
    source.source_name = name
    source.needs_enrichment = False
    source.search = AsyncMock(return_value=(jobs, len(jobs)))
    return source


def _make_matching_llm(score: float) -> MagicMock:
    """Fake matching LLM that always returns the given score."""

    def fake_invoke(messages, **kwargs):
        resp = MagicMock()
        resp.tool_calls = [
            {
                "name": "record_score",
                "args": {
                    "score": score,
                    "rationale": f"Candidate is a {int(score * 100)}% fit for this role.",
                    "strengths": ["strong Python background", "relevant domain experience"],
                    "gaps": ["no Go experience"],
                },
            }
        ]
        return resp

    llm = MagicMock()
    llm.invoke = fake_invoke
    bound = MagicMock()
    bound.invoke = fake_invoke
    bound.ainvoke = AsyncMock(side_effect=fake_invoke)
    llm.bind_tools.return_value = bound
    return llm


@pytest.mark.asyncio
async def test_sync_scores_and_displays_matches(test_app, monkeypatch):
    """
    Full flow: sync 2 jobs → score runs → GET /api/applications returns match fields.
    Also verifies GET /api/applications/{id} detail includes all match fields.
    """
    jobs = [
        _make_job_data(0, title="Senior Python Engineer"),
        _make_job_data(1, title="Backend Developer"),
    ]
    mock_source = _mock_source("adzuna", jobs)
    empty_source = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_source)
    monkeypatch.setattr("app.agents.matching_agent.get_llm", lambda: _make_matching_llm(score=0.85))

    # Trigger sync + scoring (background task runs synchronously under ASGITransport)
    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200

    # List matches
    resp = await test_app.get("/api/applications?status=pending_review")
    assert resp.status_code == 200
    apps = resp.json()
    assert len(apps) == 2

    for app in apps:
        assert app["match_score"] is not None, "match_score must be set"
        assert app["match_rationale"] is not None, "match_rationale must be set"
        assert isinstance(app["match_strengths"], list) and len(app["match_strengths"]) > 0
        assert isinstance(app["match_gaps"], list) and len(app["match_gaps"]) > 0
        assert app["job"] is not None

    # Detail endpoint also includes all match fields
    app_id = apps[0]["id"]
    detail_resp = await test_app.get(f"/api/applications/{app_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["match_score"] is not None
    assert detail["match_rationale"] is not None
    assert len(detail["match_strengths"]) > 0
    assert len(detail["match_gaps"]) > 0


@pytest.mark.asyncio
async def test_below_threshold_not_in_pending_list(test_app, monkeypatch):
    """
    Jobs scored below threshold are stored as auto_rejected and excluded
    from GET /api/applications?status=pending_review.
    """
    jobs = [_make_job_data(0, title="Irrelevant Role")]
    mock_source = _mock_source("adzuna", jobs)
    empty_source = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_source)
    monkeypatch.setattr("app.agents.matching_agent.get_llm", lambda: _make_matching_llm(score=0.3))

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200

    resp = await test_app.get("/api/applications?status=pending_review")
    assert resp.status_code == 200
    apps = resp.json()
    assert len(apps) == 0, "Below-threshold job must not appear in pending_review list"


@pytest.mark.asyncio
async def test_matches_ordered_by_score_desc(test_app, monkeypatch):
    """
    Matches are returned ordered by match_score descending.
    Two jobs with different scores: higher score appears first.
    """
    jobs = [_make_job_data(0, title="Low Score Job"), _make_job_data(1, title="High Score Job")]
    mock_source = _mock_source("adzuna", jobs)
    empty_source = _mock_source("jsearch", [])

    monkeypatch.setattr("app.services.job_sync_service.AdzunaSource", lambda: mock_source)
    monkeypatch.setattr("app.services.job_sync_service.JSearchSource", lambda: empty_source)

    # Alternate scores: job 0 → 0.7, job 1 → 0.9
    call_count = [0]

    def alternating_llm():
        scores = [0.7, 0.9]

        def fake_invoke(messages, **kwargs):
            score = scores[call_count[0] % len(scores)]
            call_count[0] += 1
            resp = MagicMock()
            resp.tool_calls = [
                {
                    "name": "record_score",
                    "args": {
                        "score": score,
                        "rationale": f"Score {score}",
                        "strengths": ["ok"],
                        "gaps": [],
                    },
                }
            ]
            return resp

        llm = MagicMock()
        llm.invoke = fake_invoke
        bound = MagicMock()
        bound.invoke = fake_invoke
        bound.ainvoke = AsyncMock(side_effect=fake_invoke)
        llm.bind_tools.return_value = bound
        return llm

    monkeypatch.setattr("app.agents.matching_agent.get_llm", alternating_llm)

    resp = await test_app.post("/api/jobs/sync")
    assert resp.status_code == 200

    resp = await test_app.get("/api/applications?status=pending_review")
    assert resp.status_code == 200
    apps = resp.json()
    assert len(apps) == 2

    scores = [a["match_score"] for a in apps]
    assert scores == sorted(scores, reverse=True), f"Expected descending scores, got {scores}"
