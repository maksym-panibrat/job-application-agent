"""
Audit + optionally recover Application rows stuck in match_status='error'.

Used after incidents that wrongly drove apps to 'error' — the canonical case
is the 2026-05-04 Gemini credit-depletion window (#75): while the
BudgetExhausted graceful path wasn't yet deployed (#73, #74), every
match_queue tick called mark_attempt_failed on every claimed app; after 3
attempts the app flipped to status='error' and stopped retrying.

Usage:

    # Default: audit only (read-only). Counts error apps grouped by profile.
    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/recover_error_matches.py

    # Audit since a given date (created_at-based; updated_at has no onupdate).
    DATABASE_URL=... uv run python scripts/recover_error_matches.py \
        --since 2026-05-04T06:00:00Z

    # Apply the recovery — re-queue all error apps to pending_match.
    DATABASE_URL=... uv run python scripts/recover_error_matches.py --apply

    # Restrict to a single profile.
    DATABASE_URL=... uv run python scripts/recover_error_matches.py \
        --profile-id 7fda7e3b-...

Recovery is idempotent: matched / pending_match / dismissed apps are
untouched. Only `match_status='error'` rows are flipped.

CRITICAL — DATABASE_URL must point at the right environment. Like
`make migrate`, this script intentionally has no automatic prod-guard;
double-check the URL host before passing --apply.
"""

import argparse
import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_session_factory  # noqa: E402
from app.services import match_queue_service  # noqa: E402


async def run(since: datetime | None, profile_id: uuid.UUID | None, apply: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        rows = await match_queue_service.audit_error_apps(
            session, since=since, profile_id=profile_id
        )

    if not rows:
        print("No applications in match_status='error' match the filters.")
        return 0

    print(f"Audit: {len(rows)} profile(s) with error-status applications")
    print(f"  {'profile_id':<38} {'count':>5}  {'oldest':<26} {'newest':<26}")
    total = 0
    for row in rows:
        total += row["count"]
        print(
            f"  {str(row['profile_id']):<38} {row['count']:>5}  "
            f"{row['oldest'].isoformat() if row['oldest'] else '-':<26} "
            f"{row['newest'].isoformat() if row['newest'] else '-':<26}"
        )
    print(f"  total: {total} application(s)")

    if not apply:
        print("\nDry run (audit only). Re-run with --apply to recover.")
        return 0

    print("\nApplying recovery: error → pending_match, attempts=0, queued_at=now()…")
    async with factory() as session:
        affected = await match_queue_service.recover_error_apps(
            session, since=since, profile_id=profile_id
        )
    print(
        f"Recovered {affected} application(s). "
        "They will be re-claimed on the next match_queue tick."
    )
    return 0


def _parse_iso(s: str) -> datetime:
    # accept trailing Z (Python <3.11 doesn't, but fromisoformat in 3.12 does)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--since",
        type=_parse_iso,
        help="Filter by Application.created_at >= ISO8601 (e.g. 2026-05-04T06:00:00Z).",
    )
    parser.add_argument(
        "--profile-id",
        type=uuid.UUID,
        help="Only consider apps for this profile.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the recovery. Without this flag, audit only.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.since, args.profile_id, args.apply)))
