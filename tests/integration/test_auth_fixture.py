"""Smoke test for the seeded_user + auth_headers fixtures."""

import jwt


async def test_seeded_user_creates_user_and_profile(seeded_user):
    user, profile = seeded_user
    assert user.id == profile.user_id
    assert user.email.endswith("@local")
    assert profile.email == user.email


async def test_auth_headers_emits_bearer_token_with_correct_claims(seeded_user, auth_headers):
    user, _ = seeded_user
    assert "Authorization" in auth_headers
    raw = auth_headers["Authorization"]
    assert raw.startswith("Bearer ")
    token = raw.split(" ", 1)[1]
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["sub"] == str(user.id)
    assert payload["aud"] == ["fastapi-users:auth"]
