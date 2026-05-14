"""Unit tests for deterministic remote policy guard."""

from types import SimpleNamespace

from app.services.remote_policy import evaluate_remote_policy


def _profile(target_locations: list[str]) -> SimpleNamespace:
    return SimpleNamespace(target_locations=target_locations)


def test_remote_only_profile_rejects_required_office_attendance():
    profile = _profile(target_locations=[])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="This role requires a minimum 3 days/week in the Toronto office.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True
    assert verdict.gap is not None
    assert "office attendance" in verdict.gap


def test_target_location_allows_matching_hybrid_office():
    profile = _profile(target_locations=["Toronto"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="This role requires a minimum 3 days/week in the Toronto office.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is False


def test_provider_remote_does_not_override_jd_office_requirement():
    profile = _profile(target_locations=[])
    job = SimpleNamespace(
        location="Remote",
        workplace_type="remote",
        description="Remote role, but candidates must work from the NYC office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_provider_remote_location_does_not_satisfy_target_location_allowlist():
    profile = _profile(target_locations=["Remote - US"])
    job = SimpleNamespace(
        location="Remote - US",
        workplace_type="remote",
        description="Remote role, but candidates must work from the NYC office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_hybrid_schedule_required_is_office_attendance():
    profile = _profile(target_locations=[])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Hybrid schedule required.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_must_be_located_near_target_city_is_office_attendance():
    profile = _profile(target_locations=[])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidates must be located near New York.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_hybrid_metadata_with_unrelated_requirement_is_not_office_attendance():
    profile = _profile(target_locations=[])
    job = SimpleNamespace(
        location="Berlin, Germany",
        workplace_type="hybrid",
        description="5+ yrs Python required.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is False


def test_short_target_location_us_does_not_match_inside_must():
    profile = _profile(target_locations=["US"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the Toronto office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_short_target_location_ca_does_not_match_inside_candidate():
    profile = _profile(target_locations=["CA"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the Toronto office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_multi_word_target_location_matches_token_boundary_text():
    profile = _profile(target_locations=["New York"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the New York office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is False
