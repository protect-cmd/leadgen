"""Diagnose SearchBug yield for Ohio filings.

Pulls a sample of OH lead_contacts rows (or filings directly) and runs
SearchBug against them, categorising the result by status. This helps
identify whether the 0/66 yield is due to common surnames, cache misses,
SearchBug data gaps, or a name-parsing issue specific to OH court formatting.

Usage:
    python scripts/diagnose_oh_searchbug.py --dry-run --limit 20
    python scripts/diagnose_oh_searchbug.py --live --limit 20
    python scripts/diagnose_oh_searchbug.py --live --county Franklin --limit 50

NOTE: --live makes real SearchBug API calls and counts against the daily cap.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Show which filings would be tested, without calling SearchBug")
    mode.add_argument("--live", action="store_true",
                      help="Make real SearchBug calls (counts against daily cap)")

    parser.add_argument("--state", default="OH")
    parser.add_argument("--county", default="Franklin")
    parser.add_argument("--limit", type=int, default=30,
                        help="Max filings to test (default 30)")
    return parser.parse_args(argv)


async def _fetch_filings(client, state: str, county: str, limit: int) -> list[dict]:
    result = (
        client.table("filings")
        .select("case_number,tenant_name,property_address,filing_date,state,county")
        .eq("state", state)
        .eq("county", county)
        .not_.is_("tenant_name", "null")
        .limit(limit)
        .execute()
    )
    return result.data or []


def _parse_address_fields(row: dict) -> tuple[str, str, str, str]:
    """Return (city, state, postal, street) as would be sent to SearchBug."""
    import re
    from services.searchbug_service import query_street_address

    addr = row.get("property_address") or ""
    parts = [p.strip() for p in addr.split(",")]
    city = parts[-2] if len(parts) >= 2 else ""
    state = row.get("state") or "OH"
    postal = ""
    if parts:
        m = re.search(r"\b([A-Z]{2})\s+(\d{5})\b", parts[-1], re.IGNORECASE)
        if m:
            state = m.group(1).upper()
            postal = m.group(2)
    street = query_street_address(addr)
    return city, state, postal, street


def _analyse_names(rows: list[dict]) -> None:
    from services.name_utils import parse_name, is_common_surname, split_tenants

    header = (
        f"{'CASE':25s} {'FIRST':14s} {'LAST':20s} "
        f"{'CITY':15s} {'ST':3s} {'ZIP':6s} {'STREET':30s} {'FILTER'}"
    )
    print(f"\n{header}")
    print("-" * len(header) + "-" * 10)
    filter_counts: Counter[str] = Counter()

    for row in rows:
        raw = row.get("tenant_name") or ""
        city, state, postal, street = _parse_address_fields(row)
        for segment in split_tenants(raw):
            first, last = parse_name(segment)
            if not first or not last:
                tag = "unparseable"
            elif is_common_surname(last):
                tag = "common_surname"
            else:
                tag = "ok"
            filter_counts[tag] += 1
            print(
                f"{row['case_number'][:25]:25s} {(first or ''):14s} {(last or ''):20s} "
                f"{city[:15]:15s} {state:3s} {postal:6s} {street[:30]:30s} {tag}"
            )

    print("\nFilter summary:")
    for tag, count in sorted(filter_counts.items(), key=lambda x: -x[1]):
        print(f"  {tag:20s} {count}")


async def _run_live(rows: list[dict]) -> None:
    from services.searchbug_service import search_tenant_detailed, reset_circuit_breaker_for_tests
    from services.name_utils import parse_name, is_common_surname, split_tenants
    from services.enrichment_cache import get_cache

    reset_circuit_breaker_for_tests()
    results: list[tuple[str, str, str]] = []  # (case_number, name, status)
    status_counts: Counter[str] = Counter()
    cache = get_cache()
    cap = 100  # diagnostic cap per run of this script

    calls_made = 0

    for row in rows:
        raw = row.get("tenant_name") or ""
        city, state, postal, query_addr = _parse_address_fields(row)

        for segment in split_tenants(raw):
            first, last = parse_name(segment)
            if not first or not last:
                status = "unparseable"
                status_counts[status] += 1
                results.append((row["case_number"], segment, status))
                print(f"  {row['case_number']:25s} {segment[:30]:30s} -> {status}")
                continue
            if is_common_surname(last):
                status = "common_surname"
                status_counts[status] += 1
                results.append((row["case_number"], segment, status))
                print(f"  {row['case_number']:25s} {first} {last:<20s} -> {status} (skipped)")
                continue

            if calls_made >= cap:
                status = "cap_hit"
                status_counts[status] += 1
                results.append((row["case_number"], segment, status))
                continue

            print(
                f"  {row['case_number']:25s} QUERY: first={first!r} last={last!r} "
                f"city={city!r} state={state!r} postal={postal!r} address={query_addr!r}"
            )
            result = await search_tenant_detailed(
                first, last, city, state, postal, address=query_addr
            )
            calls_made += 1
            cache.increment_daily_count()

            status_counts[result.status] += 1
            results.append((row["case_number"], segment, result.status))
            phone_yn = f"phone={result.phone}" if result.phone else "no_phone"
            print(
                f"  {row['case_number']:25s} RESULT: status={result.status:<20s} "
                f"{phone_yn}  rows={result.rows}"
            )

    print(f"\n{calls_made} SearchBug calls made.")
    print("\nStatus breakdown:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        pct = 100 * count / max(1, len(results))
        print(f"  {status:25s} {count:4d}  ({pct:.0f}%)")

    phone_hits = sum(1 for _, _, s in results if s == "phone_found")
    print(f"\nPhone yield: {phone_hits}/{len(results)} ({100*phone_hits/max(1,len(results)):.0f}%)")


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    from services.dedup_service import _client as supabase_client

    print(f"Fetching {args.limit} OH/{args.county} filings...")
    rows = await _fetch_filings(supabase_client, args.state, args.county, args.limit)
    print(f"Got {len(rows)} filings")

    if not rows:
        print("No rows found.")
        return 0

    if args.dry_run:
        print("\n--- Name-parsing analysis (no SearchBug calls) ---")
        _analyse_names(rows)
        return 0

    print("\n--- Live SearchBug diagnosis ---")
    await _run_live(rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
