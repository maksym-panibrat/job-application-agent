"""Unit tests for deterministic remote policy guard."""

from types import SimpleNamespace

from app.services.remote_policy import evaluate_remote_policy


def test_remote_only_profile_rejects_required_office_attendance():
    profile = SimpleNamespace(remote_ok=True, target_locations=[])
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
    profile = SimpleNamespace(remote_ok=True, target_locations=["Toronto"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="This role requires a minimum 3 days/week in the Toronto office.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is False


def test_provider_remote_does_not_override_jd_office_requirement():
    profile = SimpleNamespace(remote_ok=True, target_locations=[])
    job = SimpleNamespace(
        location="Remote",
        workplace_type="remote",
        description="Remote role, but candidates must work from the NYC office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_short_target_location_us_does_not_match_inside_must():
    profile = SimpleNamespace(remote_ok=True, target_locations=["US"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the Toronto office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_short_target_location_ca_does_not_match_inside_candidate():
    profile = SimpleNamespace(remote_ok=True, target_locations=["CA"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the Toronto office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is True


def test_multi_word_target_location_matches_token_boundary_text():
    profile = SimpleNamespace(remote_ok=True, target_locations=["New York"])
    job = SimpleNamespace(
        location=None,
        workplace_type=None,
        description="Candidate must work from the New York office twice a week.",
        description_raw=None,
    )

    verdict = evaluate_remote_policy(profile, job)

    assert verdict.hard_mismatch is False
