"""Pure-function unit tests for company_resolver.

DB-backed tests (cache hit, INSERT ... ON CONFLICT, fan-out integration) live
under tests/integration/test_company_resolver.py.
"""

from app.services import company_resolver


def test_normalize_strips_case_and_whitespace_and_hyphenates():
    assert company_resolver._normalize("  Linear  ") == "linear"
    assert company_resolver._normalize("Meta Platforms") == "meta-platforms"
    assert company_resolver._normalize("ByteDance") == "bytedance"
    assert company_resolver._normalize("Acme   Corp") == "acme-corp"
