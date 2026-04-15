import pytest

from app.sources.ats_detection import (
    detect_ats_type,
    extract_greenhouse_board_token,
    supports_api_apply,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acmecorp/jobs/1234567", "greenhouse"),
        ("https://boards.greenhouse.io/acmecorp", "greenhouse"),
        ("https://jobs.lever.co/acmecorp/abc-123", "lever"),
        ("https://lever.co/acmecorp", "lever"),
        ("https://jobs.ashbyhq.com/acmecorp/abc-123", "ashby"),
        ("https://ashby.io/acmecorp", "ashby"),
        ("https://careers.google.com/jobs/results/123", None),
        ("https://example.com/careers", None),
    ],
)
def test_detect_ats_type(url, expected):
    assert detect_ats_type(url) == expected


@pytest.mark.parametrize(
    "url, expected_token",
    [
        ("https://boards.greenhouse.io/acmecorp/jobs/1234567", "acmecorp"),
        ("https://boards.greenhouse.io/my-company/jobs/999", "my-company"),
        ("https://boards.greenhouse.io/acmecorp", "acmecorp"),
        ("https://jobs.lever.co/acmecorp/abc", None),
        ("https://example.com", None),
    ],
)
def test_extract_greenhouse_board_token(url, expected_token):
    assert extract_greenhouse_board_token(url) == expected_token


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acmecorp/jobs/1234567", True),
        ("https://jobs.lever.co/acmecorp/abc", False),
        ("https://jobs.ashbyhq.com/acmecorp/abc", False),
        ("https://example.com/careers", False),
    ],
)
def test_supports_api_apply(url, expected):
    assert supports_api_apply(url) == expected
