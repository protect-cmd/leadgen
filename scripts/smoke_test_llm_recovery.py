"""Smoke test for the LLM recovery service.

Pulls regex-rejected filings (those that would fail gate_address or gate_name)
from Supabase, runs them through the LLM, and prints the cleaned output side-
by-side with the raw input. Does NOT write anything to Supabase.

Usage:
    python scripts/smoke_test_llm_recovery.py --limit 20
    python scripts/smoke_test_llm_recovery.py --limit 20 --state OH --county Franklin
    python scripts/smoke_test_llm_recovery.py --sample-only --limit 50   # show inputs, no LLM
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="OH")
    parser.add_argument("--county", default="Franklin")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--sample-only", action="store_true",
        help="Print the regex-rejected sample without calling the LLM",
    )
    return parser.parse_args(argv)


async def _fetch_candidates(client, state: str, county: str, limit: int) -> list[dict]:
    """Pull filings, then filter locally to those that fail gate_address or
    gate_name. We over-fetch and filter in Python because gate logic isn't
    expressible cleanly in a Supabase query."""
    from pipeline import gates

    fetched_rows = (
        client.table("filings")
        .select("case_number,tenant_name,property_address,state,county")
        .eq("state", state)
        .eq("county", county)
        .not_.is_("tenant_name", "null")
        .limit(limit * 6)  # over-fetch because most will pass the gates
        .execute()
        .data
        or []
    )

    rejected: list[dict] = []
    for row in fetched_rows:
        addr = row.get("property_address") or ""
        name = row.get("tenant_name") or ""
        addr_pass = gates.gate_address(addr)
        name_pass = gates.gate_name(name)
        if addr_pass and name_pass:
            continue
        row["_rejected_reason"] = (
            "address" if not addr_pass else "name"
        )
        rejected.append(row)
        if len(rejected) >= limit:
            break
    return rejected


def _print_sample(rows: list[dict]) -> None:
    print(f"\n{'CASE':25s} {'REASON':10s} {'RAW NAME':30s} RAW ADDRESS")
    print("-" * 120)
    for row in rows:
        print(
            f"{row['case_number'][:25]:25s} "
            f"{row['_rejected_reason']:10s} "
            f"{(row.get('tenant_name') or '')[:30]:30s} "
            f"{(row.get('property_address') or '')[:60]}"
        )


async def _run_llm(rows: list[dict]) -> None:
    from services import llm_recovery_service

    if not llm_recovery_service.is_enabled():
        print("\n[WARNING] LLM_RECOVERY_ENABLED is not 'true' in your env.")
        print("Set it to true to run this smoke test.")
        return

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("\n[ERROR] OPENROUTER_API_KEY is not set in .env.")
        return

    print(f"\nRunning {len(rows)} filings through LLM recovery "
          f"(model={os.environ.get('LLM_RECOVERY_MODEL', 'qwen/qwen-2.5-7b-instruct')})...\n")

    recovered = 0
    declined = 0
    low_conf = 0
    verdict_counts: Counter[str] = Counter()

    for row in rows:
        raw_name = row.get("tenant_name") or ""
        raw_addr = row.get("property_address") or ""
        result = await llm_recovery_service.recover(raw_name, raw_addr, row.get("state") or "")

        if result.confidence < llm_recovery_service.RECOVERY_CONFIDENCE_THRESHOLD:
            verdict = "LOW_CONF"
            low_conf += 1
        elif result.skip_reason:
            verdict = "DECLINED"
            declined += 1
        else:
            verdict = "RECOVERED"
            recovered += 1
        verdict_counts[verdict] += 1

        print(
            f"[{verdict:9s}] {row['case_number']:25s} conf={result.confidence:.2f} "
            f"reason={row['_rejected_reason']}"
        )
        print(f"  RAW  : name={raw_name!r}")
        print(f"         addr={raw_addr!r}")
        print(
            f"  CLEAN: name={result.formatted_name!r}  "
            f"addr={result.formatted_address!r}"
        )
        if result.skip_reason:
            print(f"  SKIP : {result.skip_reason}")
        print()

    total = max(1, recovered + declined + low_conf)
    print("=" * 70)
    print("Smoke test summary:")
    print(f"  Recovered (would proceed):  {recovered:3d} ({100*recovered/total:.0f}%)")
    print(f"  Declined (skip_reason set): {declined:3d} ({100*declined/total:.0f}%)")
    print(f"  Low confidence (<0.7):      {low_conf:3d} ({100*low_conf/total:.0f}%)")


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    from services.dedup_service import _client as supabase_client

    print(f"Fetching regex-rejected filings: state={args.state!r} county={args.county!r}")
    rows = await _fetch_candidates(supabase_client, args.state, args.county, args.limit)
    print(f"Found {len(rows)} regex-rejected filings")

    if not rows:
        print("Nothing to test.")
        return 0

    _print_sample(rows)

    if args.sample_only:
        return 0

    await _run_llm(rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
