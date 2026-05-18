"""Backfill salary fields for existing matched jobs without LLM re-scoring.

Dry-run first:
    uv run python scripts/backfill_job_salaries.py

Apply:
    uv run python scripts/backfill_job_salaries.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_session_factory
from app.services.job_salary_backfill import backfill_job_salaries, cleanup_invalid_salaries


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write salary values to jobs.")
    parser.add_argument(
        "--no-refetch",
        action="store_true",
        help="Only parse stored description_raw; skip ATS refetch for structured salary data.",
    )
    parser.add_argument(
        "--cleanup-invalid",
        action="store_true",
        help="Null ambiguous existing salary values, such as 'Salary', '10-20', or '0-0'.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum matched jobs to scan.")
    args = parser.parse_args()

    try:
        factory = get_session_factory()
    except ValidationError as exc:
        if any(error.get("loc") == ("database_url",) for error in exc.errors()):
            print(
                "DATABASE_URL is required. Export it or create .env before running this script.",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        raise

    async with factory() as session:
        if args.cleanup_invalid:
            result = await cleanup_invalid_salaries(
                session,
                apply=args.apply,
                limit=args.limit,
            )
        else:
            result = await backfill_job_salaries(
                session,
                apply=args.apply,
                fetch_structured=not args.no_refetch,
                limit=args.limit,
            )

    mode = "APPLIED" if args.apply else "DRY RUN"
    action = "cleanup" if args.cleanup_invalid else "backfill"
    print(f"{mode}: scanned={result.scanned} updated={result.updated} unchanged={result.unchanged}")
    print(f"action: {action}")
    if not args.cleanup_invalid:
        print(
            "sources: "
            f"description={result.from_description} structured_refetch={result.from_refetch}"
        )
    if result.failed_refetches:
        print("failed_refetches:")
        for failure in result.failed_refetches:
            print(f"  - {failure}")


if __name__ == "__main__":
    asyncio.run(main())
