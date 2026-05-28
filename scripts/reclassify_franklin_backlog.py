"""Reclassify Franklin County (OH) filings that have NULL lead_bucket.

These 1,719 rows were scraped before the qualification pipeline was wired up
for OH. With CAPTURE_EXPANDED_ZIPS semantics (the current default), any ZIP
not in the legacy allowlist lands in lead_bucket='captured' rather than
'discarded', making them promotable inventory.

Usage:
    python scripts/reclassify_franklin_backlog.py --dry-run
    python scripts/reclassify_franklin_backlog.py --yes-write-supabase
    python scripts/reclassify_franklin_backlog.py --yes-write-supabase --state OH --county Franklin
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", default="OH",
        help="State filter (default: OH)",
    )
    parser.add_argument(
        "--county", default="Franklin",
        help="County filter (default: Franklin)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be updated without writing to Supabase",
    )
    parser.add_argument(
        "--yes-write-supabase", action="store_true",
        help="Required flag to actually write updates to Supabase",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Supabase fetch page size (default: 500)",
    )
    args = parser.parse_args(argv)
    if not args.dry_run and not args.yes_write_supabase:
        parser.error("Pass --dry-run to preview changes, or --yes-write-supabase to apply them.")
    return args


async def _fetch_unclassified(
    client,
    state: str,
    county: str,
    batch_size: int,
) -> list[dict]:
    """Fetch all filings with NULL lead_bucket for the given state/county."""
    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table("filings")
            .select("case_number,property_address,filing_date,state,county")
            .eq("state", state)
            .eq("county", county)
            .is_("lead_bucket", "null")
            .range(offset, offset + batch_size - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
    return rows


async def _classify_rows(rows: list[dict]) -> list[tuple[dict, str, str]]:
    """Return (row, lead_bucket, qualification_notes) for each row."""
    from pipeline.qualification import classify_lead

    today = date.today()
    classified: list[tuple[dict, str, str]] = []
    for row in rows:
        filing_date_raw = row.get("filing_date") or ""
        try:
            filing_date = date.fromisoformat(filing_date_raw)
        except ValueError:
            filing_date = today  # treat malformed dates as today to avoid discarding

        outcome = classify_lead(
            state=row["state"],
            property_address=row.get("property_address") or "",
            filing_date=filing_date,
            today=today,
            capture_expanded=True,
        )
        classified.append((row, outcome.lead_bucket, outcome.qualification_notes))
    return classified


async def _apply_updates(client, classified: list[tuple[dict, str, str]]) -> int:
    """Write lead_bucket and qualification_notes to Supabase. Returns update count."""
    from pipeline.qualification import QualificationOutcome, extract_property_zip

    updated = 0
    for row, lead_bucket, qualification_notes in classified:
        client.table("filings").update({
            "lead_bucket": lead_bucket,
            "qualification_notes": qualification_notes,
        }).eq("case_number", row["case_number"]).execute()
        updated += 1
    return updated


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    from services.dedup_service import _client as supabase_client

    print(f"Fetching unclassified filings: state={args.state!r} county={args.county!r}")
    rows = await _fetch_unclassified(supabase_client, args.state, args.county, args.batch_size)
    print(f"Found {len(rows):,} unclassified filings")

    if not rows:
        print("Nothing to do.")
        return 0

    classified = await _classify_rows(rows)

    bucket_counts: Counter[str] = Counter()
    for _, lead_bucket, _ in classified:
        bucket_counts[lead_bucket] += 1

    print("\nClassification preview:")
    for bucket, count in sorted(bucket_counts.items(), key=lambda x: -x[1]):
        print(f"  {bucket:30s} {count:,}")
    print(f"  {'TOTAL':30s} {len(classified):,}")

    if args.dry_run:
        print("\n[DRY RUN] No changes written. Pass --yes-write-supabase to apply.")
        return 0

    print(f"\nWriting {len(classified):,} updates to Supabase...")
    updated = await _apply_updates(supabase_client, classified)
    print(f"Done. {updated:,} rows updated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
