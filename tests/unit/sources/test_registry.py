"""Tests for the source registry."""

from app.sources import SOURCES
from app.sources.ashby_board import AshbyBoardSource
from app.sources.base import JobSource
from app.sources.greenhouse_board import GreenhouseBoardSource
from app.sources.lever_postings import LeverPostingsSource


def test_registry_keys_are_bare_provider_names():
    assert set(SOURCES.keys()) == {"greenhouse", "lever", "ashby"}


def test_registry_values_are_jobsource_instances():
    for value in SOURCES.values():
        assert isinstance(value, JobSource)


def test_registry_maps_to_correct_classes():
    assert isinstance(SOURCES["greenhouse"], GreenhouseBoardSource)
    assert isinstance(SOURCES["lever"], LeverPostingsSource)
    assert isinstance(SOURCES["ashby"], AshbyBoardSource)
