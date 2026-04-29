import uuid

from app.models.user_profile import UserProfile
from app.services.profile_service import seed_defaults_if_empty


def test_seed_defaults_if_empty_seeds_first_5():
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {}
    changed = seed_defaults_if_empty(p)
    assert changed is True
    assert len(p.target_company_slugs["greenhouse"]) == 5


def test_seed_defaults_if_empty_no_op_when_slugs_present():
    p = UserProfile(user_id=uuid.uuid4())
    p.target_company_slugs = {"greenhouse": ["custom-co"]}
    changed = seed_defaults_if_empty(p)
    assert changed is False
    assert p.target_company_slugs["greenhouse"] == ["custom-co"]
