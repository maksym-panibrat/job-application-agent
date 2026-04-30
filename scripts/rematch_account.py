"""
One-shot helper to call POST /api/profile/rematch for a given account.

Looks up the user by email, signs a short-lived JWT (90s), and posts to
{host}/api/profile/rematch. Prints the response JSON (e.g. {"reset": 47}).

CRITICAL — JWT_SECRET must match the deployed environment's secret, or
prod returns 401 "Invalid token". Local .env typically holds a dev secret
that prod will reject. Pass the prod secret explicitly:

    JWT_SECRET=$(gcloud secrets versions access latest --secret=jwt-secret) \\
        uv run python scripts/rematch_account.py \\
            --host https://job-application-agent-XXX-uc.a.run.app \\
            [--email maksym@panibrat.com]

pydantic-settings prefers env vars over .env, so the inline JWT_SECRET wins.

Defaults to the configured user's email; --host is required (varies per Cloud Run deploy).
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import jwt
from sqlmodel import select

from app.config import get_settings
from app.database import get_session_factory
from app.models.user import User

DEFAULT_EMAIL = "maksym@panibrat.com"


async def lookup_user_id(email: str) -> str:
    factory = get_session_factory()
    async with factory() as s:
        # User has `oauth_accounts: list[...] = Relationship(lazy="joined")` in the
        # model — the LEFT JOIN can yield multiple rows per user (one per oauth
        # account), so .unique() must be called before scalar_one_or_none().
        result = await s.execute(select(User).where(User.email == email))
        u = result.unique().scalar_one_or_none()
    if not u:
        raise SystemExit(f"No user with email {email!r}")
    return str(u.id)


def sign_token(user_id: str, secret: str, ttl_seconds: int = 90) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "aud": ["fastapi-users:auth"],
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=ttl_seconds)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


async def main(host: str, email: str) -> None:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    if secret == "dev-secret":
        print(
            "ERROR: jwt_secret is the dev default — prod will reject the token.",
            "Re-run with: JWT_SECRET=$(gcloud secrets versions access latest "
            "--secret=jwt-secret) uv run python scripts/rematch_account.py ...",
            sep="\n",
            file=sys.stderr,
        )
        raise SystemExit(2)

    user_id = await lookup_user_id(email)
    token = sign_token(user_id, secret)

    url = f"{host.rstrip('/')}/api/profile/rematch"
    print(f"POST {url}  (user={email})")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {token}"})
    print(f"HTTP {resp.status_code}")
    print(resp.text)
    if resp.status_code >= 400:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Base URL, e.g. https://...run.app")
    parser.add_argument(
        "--email",
        default=DEFAULT_EMAIL,
        help=f"Account email (default {DEFAULT_EMAIL})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.host, args.email))
