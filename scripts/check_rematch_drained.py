"""
Verify a profile's rematch has fully drained.

Reports:
1. Count of applications still in pending_match (should be ~0).
2. Count of applications scored in the last 60 minutes (should match the rematch size).
3. 3 sample match_summary values to spot-check the new prompt's output.

Usage:
    PYTHONPATH=. uv run python scripts/check_rematch_drained.py [--email maksym@panibrat.com]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.database import get_session_factory

DEFAULT_EMAIL = "maksym@panibrat.com"


async def main(email: str) -> None:
    factory = get_session_factory()
    async with factory() as s:
        row = (
            await s.execute(
                text("""
                    SELECT p.id::text AS profile_id
                    FROM user_profiles p
                    JOIN users u ON u.id = p.user_id
                    WHERE u.email = :email
                """),
                {"email": email},
            )
        ).fetchone()
        if not row:
            raise SystemExit(f"No profile for {email!r}")
        pid = row.profile_id
        print(f"Profile: {pid}  ({email})")
        print()

        pending = (
            await s.execute(
                text("""
                    SELECT count(*) FROM applications
                    WHERE profile_id = :pid AND match_status = 'pending_match'
                """),
                {"pid": pid},
            )
        ).scalar()
        scored_recent = (
            await s.execute(
                text("""
                    SELECT count(*) FROM applications
                    WHERE profile_id = :pid
                      AND match_score IS NOT NULL
                      AND updated_at > now() - interval '60 minutes'
                """),
                {"pid": pid},
            )
        ).scalar()
        scored_total = (
            await s.execute(
                text("""
                    SELECT count(*) FROM applications
                    WHERE profile_id = :pid AND match_score IS NOT NULL
                """),
                {"pid": pid},
            )
        ).scalar()
        errored = (
            await s.execute(
                text("""
                    SELECT count(*) FROM applications
                    WHERE profile_id = :pid AND match_status = 'error'
                """),
                {"pid": pid},
            )
        ).scalar()

        print("Queue state:")
        print(f"  pending_match    : {pending}  (target: 0)")
        print(f"  match_status=error : {errored}")
        print(f"  scored in last hour: {scored_recent}")
        print(f"  scored total       : {scored_total}")
        print()

        samples = (
            await s.execute(
                text("""
                    SELECT match_score, match_summary, match_rationale, length(match_summary) AS len
                    FROM applications
                    WHERE profile_id = :pid
                      AND match_summary IS NOT NULL
                      AND updated_at > now() - interval '60 minutes'
                    ORDER BY updated_at DESC
                    LIMIT 3
                """),
                {"pid": pid},
            )
        ).fetchall()
        print("Recent summaries (spot-check terseness — target ≤12 words):")
        for sc, summ, rat, n in samples:
            words = len(summ.split()) if summ else 0
            print(f"  [{sc:.2f}] ({words}w/{n}c) {summ!r}")
            print(f"        rationale: {rat!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    args = parser.parse_args()
    asyncio.run(main(args.email))
