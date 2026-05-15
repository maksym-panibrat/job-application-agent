"""Unit tests for deterministic remote policy guard."""

from types import SimpleNamespace

from app.services.remote_policy import evaluate_remote_policy, evaluate_us_location_policy


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


def test_us_location_policy_allows_explicit_us_signal():
    job = SimpleNamespace(
        location="Remote - United States",
        workplace_type="remote",
        description="Applicants must be authorized to work in the U.S.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is False


def test_us_location_policy_rejects_explicit_non_us_position():
    job = SimpleNamespace(
        location="Toronto, Canada",
        workplace_type="remote",
        description="Remote role open to candidates based in Canada.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_non_us_location_despite_us_customer_text():
    job = SimpleNamespace(
        location="Toronto, Canada",
        workplace_type="remote",
        description="Remote support role helping support US customers.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_tbilisi_georgia_as_non_us_location():
    job = SimpleNamespace(
        location="Tbilisi, Georgia",
        workplace_type="remote",
        description="Remote role for a distributed support team.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_remote_canada_unavailable_in_us():
    job = SimpleNamespace(
        location="Remote - Canada",
        workplace_type="remote",
        description="This position is not available in the United States.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_remote_canada_us_applicants_not_eligible():
    job = SimpleNamespace(
        location="Remote - Canada",
        workplace_type="remote",
        description="US applicants are not eligible.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_canadian_country_abbreviation():
    job = SimpleNamespace(
        location="Toronto, ON, CA",
        workplace_type="remote",
        description="Remote role open to candidates based in Canada.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_rejects_ambiguous_remote_position():
    job = SimpleNamespace(
        location="Remote",
        workplace_type="remote",
        description="Work from anywhere with a distributed engineering team.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is True
    assert verdict.gap == "Position is not US-based"


def test_us_location_policy_allows_state_name_signal():
    job = SimpleNamespace(
        location="Remote",
        workplace_type="remote",
        description="Candidates may work remotely from California.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is False


def test_us_location_policy_allows_state_abbreviation_signal():
    job = SimpleNamespace(
        location="San Francisco, CA",
        workplace_type="hybrid",
        description=None,
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is False


def test_us_location_policy_allows_contextual_state_abbreviation_signal():
    job = SimpleNamespace(
        location="Remote",
        workplace_type="remote",
        description="Applicants must be based in CA.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is False


def test_us_location_policy_allows_city_state_signal():
    job = SimpleNamespace(
        location=None,
        workplace_type="hybrid",
        description="Hybrid role based in New York, NY.",
        description_raw=None,
    )

    verdict = evaluate_us_location_policy(job)

    assert verdict.hard_mismatch is False
