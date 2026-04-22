"""
Helper invoked by `make smoke-token`.

Signs a 90-day JWT for smoke@panibrat.com using the same claims shape that
fastapi-users / app/api/deps.py expects:
  - alg:  HS256
  - aud:  ["fastapi-users:auth"]
  - sub:  SMOKE_USER_ID (stable UUID defined in scripts/seed_smoke_user.py)

Prints the raw token to stdout so the caller can store it as a secret.

Usage (usually via Makefile):
    JWT_SECRET=<secret> uv run python scripts/make_smoke_token.py
    # or rely on .env / pydantic settings to load JWT_SECRET
"""

import datetime
import sys

try:
    import jwt
except ImportError:
    print("ERROR: PyJWT not found. Run: uv sync --dev", file=sys.stderr)
    sys.exit(1)

# Stable smoke-user UUID — must match scripts/seed_smoke_user.py::SMOKE_USER_ID
SMOKE_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# Resolve the JWT secret via pydantic settings first; fall back to JWT_SECRET env var.
secret: str = ""
try:
    from app.config import get_settings

    secret = get_settings().jwt_secret.get_secret_value()
except Exception as exc:
    import os

    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        print(f"ERROR: Could not load jwt_secret: {exc}", file=sys.stderr)
        print(
            "Set JWT_SECRET env var or ensure DATABASE_URL / .env are present.",
            file=sys.stderr,
        )
        sys.exit(1)

if secret == "dev-secret":
    print(
        "WARNING: Using default dev-secret.  "
        "In prod, export JWT_SECRET=<real-secret> before running this command.",
        file=sys.stderr,
    )

now = datetime.datetime.now(datetime.UTC)
exp = now + datetime.timedelta(days=90)

payload = {
    "sub": SMOKE_USER_ID,
    "aud": ["fastapi-users:auth"],
    "iat": int(now.timestamp()),
    "exp": int(exp.timestamp()),
}

token = jwt.encode(payload, secret, algorithm="HS256")
print(token)
