"""
Wipe job-search data (jobs, applications, generated_documents) and the
operational counters that index against them, while preserving the
user/profile/resume rows. Useful after schema or sourcing changes leave
stale records behind that no longer reflect the current pipeline.

Run against local dev DB:
    uv run python scripts/wipe_job_data.py

Run against prod Neon (requires explicit confirmation):
    DATABASE_URL=$(gcloud secrets versions access latest --secret=database-url) \\
        uv run python scripts/wipe_job_data.py --yes-i-mean-prod

Prints row counts before and after so you can verify the wipe.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Order matters for the printed report; TRUNCATE ... CASCADE handles FKs.
WIPE_TABLES = (
    "generated_documents",
    "applications",
    "jobs",
    "llm_status",
    "rate_limits",
    "usage_counters",
)

KEEP_TABLES = (
    "users",
    "oauth_accounts",
    "user_profiles",
    "skills",
    "work_experiences",
)


async def _counts(session: AsyncSession, tables: tuple[str, ...]) -> dict[str, int]:
    out = {}
    for table in tables:
        result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
        out[table] = result.scalar_one()
    return out


async def wipe(session: AsyncSession) -> None:
    print("\nBEFORE — job-search tables:")
    for t, n in (await _counts(session, WIPE_TABLES)).items():
        print(f"  {t:25s} {n:>10,}")
    print("\nBEFORE — preserved tables:")
    for t, n in (await _counts(session, KEEP_TABLES)).items():
        print(f"  {t:25s} {n:>10,}")

    joined = ", ".join(WIPE_TABLES)
    print(f"\nTruncating: {joined}")
    await session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))  # noqa: S608
    await session.commit()

    print("\nAFTER — job-search tables:")
    for t, n in (await _counts(session, WIPE_TABLES)).items():
        print(f"  {t:25s} {n:>10,}")
    print("\nAFTER — preserved tables (should be unchanged):")
    for t, n in (await _counts(session, KEEP_TABLES)).items():
        print(f"  {t:25s} {n:>10,}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes-i-mean-prod",
        action="store_true",
        help="Required when DATABASE_URL points outside localhost.",
    )
    args = parser.parse_args()

    from urllib.parse import urlparse

    from app.config import get_settings

    settings = get_settings()
    # Pydantic v2's PostgresDsn is a MultiHostUrl — parse the string directly
    # to get a single hostname rather than depending on pydantic internals.
    db_host = urlparse(str(settings.database_url)).hostname or ""
    # `postgres` covers compose service names like our local docker-compose db.
    is_local = db_host in ("", "localhost", "127.0.0.1", "postgres")

    if not is_local and not args.yes_i_mean_prod:
        print(
            f"DATABASE_URL host '{db_host}' looks remote. Pass --yes-i-mean-prod to confirm.",
            file=sys.stderr,
        )
        sys.exit(2)

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await wipe(session)
    print("\nDone — re-run sourcing to repopulate (POST /internal/cron/sync).")


if __name__ == "__main__":
    asyncio.run(main())
