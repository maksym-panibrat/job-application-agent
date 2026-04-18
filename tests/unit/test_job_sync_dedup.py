"""Unit tests for job sync deduplication logic."""

from app.models.user_profile import UserProfile
from app.services.job_sync_service import _dedup_jobs, generate_queries
from app.sources.base import JobData


def _job(
    external_id: str,
    title: str,
    company: str,
    location: str | None = None,
    workplace_type: str | None = None,
) -> JobData:
    return JobData(
        external_id=external_id,
        title=title,
        company_name=company,
        location=location,
        workplace_type=workplace_type,
        apply_url=f"https://example.com/{external_id}",
    )


def _profile(**kwargs) -> UserProfile:
    defaults = dict(
        id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000001",
        target_roles=[],
        target_locations=[],
        search_keywords=[],
        remote_ok=True,
        source_cursors={},
        search_active=False,
    )
    defaults.update(kwargs)
    return UserProfile(**defaults)


class TestDedupJobs:
    def test_no_duplicates_unchanged(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme"),
            _job("2", "Frontend Engineer", "Globex"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 2

    def test_duplicate_locations_keeps_one(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme", "New York, NY"),
            _job("2", "Backend Engineer", "Acme", "San Francisco, CA"),
            _job("3", "Backend Engineer", "Acme", "Austin, TX"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 1

    def test_prefers_remote_variant(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme", "New York, NY"),
            _job("2", "Backend Engineer", "Acme", "Remote"),
            _job("3", "Backend Engineer", "Acme", "Austin, TX"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 1
        assert result[0].external_id == "2"

    def test_prefers_remote_via_workplace_type(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme", "New York, NY"),
            _job("2", "Backend Engineer", "Acme", "Anywhere", workplace_type="remote"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 1
        assert result[0].external_id == "2"

    def test_prefers_target_location_match(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme", "Chicago, IL"),
            _job("2", "Backend Engineer", "Acme", "San Francisco, CA"),
        ]
        result = _dedup_jobs(jobs, _profile(target_locations=["San Francisco"]))
        assert len(result) == 1
        assert result[0].external_id == "2"

    def test_case_insensitive_key(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme Corp", "New York"),
            _job("2", "BACKEND ENGINEER", "ACME CORP", "Austin"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 1

    def test_distinct_titles_not_merged(self):
        jobs = [
            _job("1", "Backend Engineer", "Acme", "New York"),
            _job("2", "Frontend Engineer", "Acme", "New York"),
        ]
        result = _dedup_jobs(jobs, _profile())
        assert len(result) == 2


class TestGenerateQueries:
    def test_no_locations_remote_ok(self):
        profile = _profile(search_keywords=["python developer"], remote_ok=True)
        queries = generate_queries(profile)
        assert queries == [("python developer", None)]

    def test_no_locations_not_remote_ok(self):
        profile = _profile(search_keywords=["python developer"], remote_ok=False)
        queries = generate_queries(profile)
        assert queries == []

    def test_with_locations(self):
        profile = _profile(
            search_keywords=["python developer"],
            target_locations=["San Francisco"],
            remote_ok=False,
        )
        queries = generate_queries(profile)
        assert queries == [("python developer", "San Francisco")]

    def test_cross_product_keywords_locations(self):
        profile = _profile(
            search_keywords=["backend", "fullstack"],
            target_locations=["New York", "Austin"],
            remote_ok=False,
        )
        queries = generate_queries(profile)
        # cross-product, capped at adzuna_max_queries_per_sync (default 3)
        assert ("backend", "New York") in queries
        assert ("backend", "Austin") in queries
        assert len(queries) <= 3

    def test_falls_back_to_target_roles(self):
        profile = _profile(target_roles=["software engineer"], remote_ok=True)
        queries = generate_queries(profile)
        assert queries[0][0] == "software engineer"

    def test_remote_string_not_passed_as_location(self):
        """'remote' must never appear as the location value — it's not a geographic place."""
        profile = _profile(search_keywords=["swe"], remote_ok=True, target_locations=[])
        queries = generate_queries(profile)
        for _, loc in queries:
            assert loc != "remote"
