"""
Compare HTML cleaning strategies for job descriptions.

Usage:
    uv run python scripts/compare_jd_cleaning.py [--sample 50] [--out tmp/jd_clean]

Pulls a stratified sample of jobs (across size buckets), runs each through three
cleaning strategies, and reports size/savings + writes artifacts for spot-checking.

Strategies compared:
  - regex_strip   : re.sub('<[^>]+>', ' ') + whitespace collapse
  - bs4_text      : BeautifulSoup .get_text(separator='\\n', strip=True)
  - markdownify   : HTML -> markdown via markdownify
"""

import argparse
import asyncio
import re
import statistics
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify
from sqlalchemy import text

from app.database import get_session_factory


def clean_regex(html: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", no_tags).strip()


def clean_bs4(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def clean_markdownify(html: str) -> str:
    md = markdownify(html, heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", md).strip()


STRATEGIES = {
    "regex_strip": clean_regex,
    "bs4_text": clean_bs4,
    "markdownify": clean_markdownify,
}


async def fetch_stratified_sample(per_bucket: int):
    """Pull jobs spread across size buckets so we see behavior on small + large + huge JDs."""
    buckets = [
        ("xs", 0, 3000),
        ("s", 3001, 6000),
        ("m", 6001, 8000),
        ("l", 8001, 12000),
        ("xl", 12001, 100000),
    ]
    factory = get_session_factory()
    rows = []
    async with factory() as session:
        for label, lo, hi in buckets:
            r = await session.execute(
                text(
                    """
                    SELECT id::text, source, title,
                           length(description_md) AS raw_len, description_md
                    FROM jobs
                    WHERE description_md IS NOT NULL
                      AND length(description_md) BETWEEN :lo AND :hi
                    ORDER BY random()
                    LIMIT :n
                    """
                ),
                {"lo": lo, "hi": hi, "n": per_bucket},
            )
            for row in r.fetchall():
                rows.append((label, row.id, row.source, row.title, row.raw_len, row.description_md))
    return rows


def fit_count(lengths: list[int], cap: int) -> int:
    return sum(1 for n in lengths if n <= cap)


async def main(sample: int, out_dir: Path) -> None:
    per_bucket = max(1, sample // 5)
    rows = await fetch_stratified_sample(per_bucket)
    if not rows:
        print("No jobs in DB — run a sync first.")
        return

    print(f"Sampled {len(rows)} jobs ({per_bucket}/bucket × 5 buckets)\n")

    # Per-job table
    header = (
        f"{'bkt':4} {'src':14} {'raw':>6} "
        + " ".join(f"{name[:11]:>11}" for name in STRATEGIES)
        + "  "
        + " ".join(f"{name[:6] + '%':>7}" for name in STRATEGIES)
    )
    print(header)
    print("-" * len(header))

    per_method_lengths: dict[str, list[int]] = {name: [] for name in STRATEGIES}
    raw_lengths: list[int] = []

    out_dir.mkdir(parents=True, exist_ok=True)

    for bucket, jid, source, title, raw_len, html in rows:
        raw_lengths.append(raw_len)
        cleaned: dict[str, str] = {}
        sizes: dict[str, int] = {}
        for name, fn in STRATEGIES.items():
            try:
                out = fn(html)
            except Exception as exc:
                out = f"[ERROR: {exc}]"
            cleaned[name] = out
            sizes[name] = len(out)
            per_method_lengths[name].append(len(out))

        size_cells = " ".join(f"{sizes[name]:>11}" for name in STRATEGIES)
        pct_cells = " ".join(f"{int((1 - sizes[name] / raw_len) * 100):>6}%" for name in STRATEGIES)
        print(f"{bucket:4} {source[:14]:14} {raw_len:>6} {size_cells}  {pct_cells}")

        # Write artifacts for the first 3 of each bucket (so manual review stays sane)
        # Decide by looking at how many we've already written for this bucket.
        bucket_dir = out_dir / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        already = len(list(bucket_dir.glob("*_raw.html")))
        if already < 3:
            stem = bucket_dir / f"{jid[:8]}_{re.sub(r'[^a-zA-Z0-9]+', '_', title)[:40]}"
            (Path(f"{stem}_raw.html")).write_text(html)
            for name, out in cleaned.items():
                ext = "md" if name == "markdownify" else "txt"
                (Path(f"{stem}_{name}.{ext}")).write_text(out)

    # Aggregate
    print()
    print("=" * 70)
    print("AGGREGATE")
    print("=" * 70)
    raw_mean = int(statistics.mean(raw_lengths))
    raw_med = int(statistics.median(raw_lengths))
    raw_fit = fit_count(raw_lengths, 8000)
    print(
        f"raw         : mean={raw_mean:>6}  median={raw_med:>6}  "
        f"fit_8k={raw_fit:>3}/{len(raw_lengths)}"
    )
    for name in STRATEGIES:
        lens = per_method_lengths[name]
        mean = int(statistics.mean(lens))
        med = int(statistics.median(lens))
        savings = int((1 - mean / raw_mean) * 100)
        fit = fit_count(lens, 8000)
        print(
            f"{name:12}: mean={mean:>6}  median={med:>6}  fit_8k={fit:>3}/{len(lens)}  "
            f"avg_savings={savings:>3}% vs raw"
        )
    print()
    print(f"Artifacts written to: {out_dir}/")
    print(
        "Spot-check a few side-by-side to compare quality "
        "(look for lost structure, broken lists, malformed bullets)."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample",
        type=int,
        default=50,
        help="Total jobs to sample (split across 5 buckets)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tmp/jd_clean"),
        help="Output dir for artifacts",
    )
    args = parser.parse_args()
    asyncio.run(main(args.sample, args.out))
