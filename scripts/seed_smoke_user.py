"""
Idempotently seed the smoke-test user (smoke@panibrat.com) into the database.

Run against local dev DB:
    uv run python scripts/seed_smoke_user.py

Run against prod Neon (in a local shell with DATABASE_URL exported):
    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/seed_smoke_user.py

Safe to re-run — uses INSERT ... ON CONFLICT DO UPDATE so it is fully idempotent.
Both AUTH_ENABLED modes are handled:
  - AUTH_ENABLED=true  → row in `users` table (fastapi-users) + `user_profiles`
  - AUTH_ENABLED=false → same smoke UUID, but auth machinery is bypassed by deps.py;
                         we still seed the user row so JWT decode works if auth is ever enabled.
"""

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Stable smoke-user identity — never change this UUID; it is the anchor for
# SMOKE_BEARER_TOKEN (the JWT's `sub` claim) stored in GitHub Actions secrets.
SMOKE_USER_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SMOKE_USER_EMAIL = "smoke@panibrat.com"


async def seed(session: AsyncSession) -> None:
    """Insert or update the smoke user row in `users` and ensure a profile row exists."""

    # Upsert into `users` (fastapi-users compatible schema)
    await session.execute(
        text(
            """
            INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified)
            VALUES (:id, :email, '', TRUE, FALSE, TRUE)
            ON CONFLICT (id) DO UPDATE
              SET email        = EXCLUDED.email,
                  is_active    = TRUE,
                  is_verified  = TRUE
            """
        ),
        {"id": SMOKE_USER_ID, "email": SMOKE_USER_EMAIL},
    )

    # Upsert a minimal profile row so profile-dependent endpoints don't 404
    await session.execute(
        text(
            """
            INSERT INTO user_profiles (
                id,
                user_id,
                full_name,
                email,
                target_roles,
                target_locations,
                remote_ok,
                source_cursors,
                target_company_slugs,
                standard_answers,
                search_active,
                created_at,
                updated_at
            )
            VALUES (
                gen_random_uuid(),
                :user_id,
                'Smoke Test',
                :email,
                '{}',
                '{}',
                TRUE,
                '{}',
                '{}',
                '{}',
                TRUE,
                NOW(),
                NOW()
            )
            ON CONFLICT (user_id) DO UPDATE
              SET full_name  = 'Smoke Test',
                  email      = EXCLUDED.email,
                  updated_at = NOW()
            """
        ),
        {"user_id": SMOKE_USER_ID, "email": SMOKE_USER_EMAIL},
    )

    await session.commit()
    print(f"Smoke user seeded: id={SMOKE_USER_ID}  email={SMOKE_USER_EMAIL}")


async def main() -> None:
    # Validate settings early (raises if DATABASE_URL is missing)
    from app.config import get_settings

    get_settings()

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await seed(session)
    print("Done — safe to re-run.")


if __name__ == "__main__":
    asyncio.run(main())
