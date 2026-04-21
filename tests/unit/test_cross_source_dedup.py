"""Unit tests for _dedup_jobs_across_sources cross-source deduplication."""

from app.services.job_sync_service import SOURCE_PREFERENCE, _dedup_jobs_across_sources
from app.sources.base import JobData


def make_job(
    title: str,
    company: str,
    apply_url: str = "https://example.com",
    workplace_type: str | None = None,
    location: str | None = None,
) -> JobData:
    return JobData(
        external_id="test",
        title=title,
        company_name=company,
        apply_url=apply_url,
        workplace_type=workplace_type,
        location=location,
    )


class TestDedupJobsAcrossSources:
    def test_greenhouse_board_wins_over_adzuna(self):
        job = make_job("Software Engineer", "Acme")
        result = _dedup_jobs_across_sources({"greenhouse_board": [job], "adzuna": [job]})
        assert len(result) == 1
        source, _ = result[0]
        assert source == "greenhouse_board"

    def test_remotive_wins_over_jsearch(self):
        job = make_job("Data Scientist", "DataCo", workplace_type="remote")
        result = _dedup_jobs_across_sources({"remotive": [job], "jsearch": [job]})
        assert len(result) == 1
        source, _ = result[0]
        assert source == "remotive"

    def test_different_location_buckets_kept_as_separate_rows(self):
        remote_job = make_job("Backend Engineer", "Acme", workplace_type="remote")
        onsite_job = make_job("Backend Engineer", "Acme", location="San Francisco, CA")
        result = _dedup_jobs_across_sources({"adzuna": [remote_job, onsite_job]})
        assert len(result) == 2

    def test_case_insensitive_keys(self):
        job_a = make_job("Software Engineer", "Acme Corp")
        job_b = make_job("software engineer", "acme corp")
        result = _dedup_jobs_across_sources({"adzuna": [job_a], "jsearch": [job_b]})
        assert len(result) == 1

    def test_punctuation_stripped_from_keys(self):
        job_a = make_job("Software Engineer, Backend", "Acme")
        job_b = make_job("Software Engineer Backend", "Acme")
        result = _dedup_jobs_across_sources({"adzuna": [job_a], "jsearch": [job_b]})
        assert len(result) == 1

    def test_empty_inputs(self):
        result = _dedup_jobs_across_sources({})
        assert result == []

    def test_single_source_passthrough(self):
        jobs = [
            make_job("Backend Engineer", "Acme"),
            make_job("Frontend Engineer", "Globex"),
            make_job("DevOps Engineer", "Initech"),
        ]
        result = _dedup_jobs_across_sources({"adzuna": jobs})
        assert len(result) == 3

    def test_all_sources_same_job_greenhouse_wins(self):
        job = make_job("Platform Engineer", "MegaCorp")
        sources = {name: [job] for name in SOURCE_PREFERENCE}
        result = _dedup_jobs_across_sources(sources)
        assert len(result) == 1
        source, _ = result[0]
        assert source == "greenhouse_board"

    def test_unknown_source_gets_preference_zero(self):
        job = make_job("ML Engineer", "StartupAI")
        result = _dedup_jobs_across_sources({"unknown_board": [job], "jsearch": [job]})
        assert len(result) == 1
        source, _ = result[0]
        assert source == "jsearch"
