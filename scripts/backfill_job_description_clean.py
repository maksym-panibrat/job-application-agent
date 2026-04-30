"""Backfill jobs.description_clean for rows where it's NULL.

Idempotent: re-running is safe (only touches NULL rows).

Usage:
    uv run python scripts/backfill_job_description_clean.py [--batch-size 200]
"""

import argparse
import asyncio

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.database import get_session_factory
from app.models.job import Job
from app.services.html_cleaner import clean_html_to_markdown

log = structlog.get_logger()


async def run_backfill(batch_size: int, session: AsyncSession) -> tuple[int, int]:
    """Process all NULL description_clean rows in batches. Returns (processed, skipped).

    `skipped` is always 0 — kept in the signature for backward compatibility.
    The cleaner is deterministic and can't raise in practice (BS html.parser is
    lenient, markdownify is lenient, regex runs on a string), so fail-fast is
    correct for this one-off script. Concurrent writes from the live sync
    worker are handled by a guarded UPDATE: if `upsert_job` set the column
    between our SELECT and UPDATE, the WHERE clause skips the row silently.
    """
    processed = 0
    skipped = 0
    while True:
        result = await session.execute(
            select(Job).where(Job.description_clean.is_(None)).limit(batch_size)
        )
        rows = list(result.scalars().all())
        if not rows:
            break
        for job in rows:
            cleaned = clean_html_to_markdown(job.description_md)
            await session.execute(
                update(Job)
                .where(Job.id == job.id, Job.description_clean.is_(None))
                .values(description_clean=cleaned)
            )
            processed += 1
        await session.commit()
        await log.ainfo("backfill.batch", processed=processed, skipped=skipped)
    return processed, skipped


async def main(batch_size: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        processed, skipped = await run_backfill(batch_size, session)
    print(f"Backfill complete. processed={processed} skipped={skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(main(args.batch_size))
