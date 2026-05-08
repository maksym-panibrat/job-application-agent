"""Contract tests for JobSource base class."""

import pytest

from app.sources.base import InvalidSlugError, JobSource, TransientFetchError


def test_jobsource_is_abstract_for_provider_name_and_fetch_jobs():
    """The base class must require provider_name and fetch_jobs; validate has a
    sensible default that subclasses can override but don't have to."""
    with pytest.raises(TypeError):
        JobSource()  # abstract — provider_name + fetch_jobs unimplemented


def test_invalid_slug_error_lives_in_base():
    """Both error types must be importable from app.sources.base — adapters
    raise these from their fetch path and the scheduler branches on them."""
    assert issubclass(InvalidSlugError, Exception)
    assert issubclass(TransientFetchError, Exception)


def test_jobsource_no_search_method():
    """search() was unused; removing it means no half-implemented flag method
    is left on the abstract class."""
    assert not hasattr(JobSource, "search"), (
        "search() should be removed from the JobSource contract"
    )


def test_jobsource_provider_name_and_fetch_jobs_are_abstract():
    """provider_name and fetch_jobs must be implemented by subclasses; they
    are part of the abstract contract."""
    assert "fetch_jobs" in JobSource.__abstractmethods__
    assert "provider_name" in JobSource.__abstractmethods__


def test_jobsource_validate_default_raises_not_implemented():
    """validate() has a default that raises — subclasses must override or
    callers must catch."""
    import asyncio

    class _Stub(JobSource):
        @property
        def provider_name(self) -> str:
            return "stub"

        async def fetch_jobs(self, slug, *, since=None, client=None):
            return []

    with pytest.raises(NotImplementedError):
        asyncio.run(_Stub().validate("x"))


def test_provider_name_returns_string():
    """Concrete adapters must implement provider_name as a property returning str."""
    from app.sources.greenhouse_board import GreenhouseBoardSource

    src = GreenhouseBoardSource()
    assert isinstance(src.provider_name, str)
    assert src.provider_name == "greenhouse_board"  # rename to "greenhouse" happens in Task B3
