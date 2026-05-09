import uuid

from app.models.user_profile import UserProfile
from app.services.profile_service import seed_defaults_if_empty


def test_seed_defaults_if_empty_is_now_no_op_for_empty_profile():
    """seed_defaults_if_empty has been retired in favor of the onboarding-agent
    + company_resolver path. It must not mutate the profile, must not write to
    the deprecated target_company_slugs JSONB, and must always return False."""
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {}
    changed = seed_defaults_if_empty(p)
    assert changed is False
    assert p.target_company_slugs == {}


def test_seed_defaults_if_empty_is_now_no_op_when_slugs_present():
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {"greenhouse": ["custom-co"]}
    changed = seed_defaults_if_empty(p)
    assert changed is False
    assert p.target_company_slugs == {"greenhouse": ["custom-co"]}
