"""Manually promote captured filings in a ZIP cohort to residential_approved
so they enter the enrichment funnel on the next runner cycle.

Usage:
    python scripts/promote_captured_zips.py --state TX --zips 77090,77042 --since 2026-05-01
    python scripts/promote_captured_zips.py --state TX --zips 77090 --dry-run
    python scripts/promote_captured_zips.py --state TX --zips 77090 --demote
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

_client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def run(state: str, zips: list[str], since: str, dry_run: bool, demote: bool) -> dict:
    source_bucket = "residential_approved" if demote else "captured"
    target_bucket = "captured" if demote else "residential_approved"

    rows = (
        _client.table("filings")
        .select("case_number, property_zip, lead_bucket, qualification_notes")
        .eq("state", state)
        .in_("property_zip", zips)
        .gte("filing_date", since)
        .execute()
        .data or []
    )
    eligible = [r for r in rows if r.get("lead_bucket") == source_bucket]
    print(f"Eligible rows: {len(eligible)} (state={state}, zips={zips}, since={since})")

    if dry_run:
        print(f"DRY RUN: would change lead_bucket={source_bucket} -> {target_bucket}")
        cost_per_call = 0.20  # rough SearchBug rate
        print(f"Projected enrichment cost if promoted: ~${len(eligible) * cost_per_call:.2f}")
        return {"projected_promotions": len(eligible), "dry_run": True}

    now = datetime.now(timezone.utc).isoformat()
    note_suffix = (
        f"Demoted to captured by promote_captured_zips on {now[:10]}."
        if demote else
        f"Promoted from captured by ZIP cohort {zips} on {now[:10]}."
    )

    changed = 0
    for row in eligible:
        new_notes = (row.get("qualification_notes") or "").rstrip(".") + ". " + note_suffix
        _client.table("filings").update({
            "lead_bucket": target_bucket,
            "qualification_notes": new_notes,
            "classified_at": now,
        }).eq("case_number", row["case_number"]).execute()
        changed += 1

    print(f"Updated {changed} rows: lead_bucket={source_bucket} -> {target_bucket}")
    return {"promoted": changed, "dry_run": False}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--zips", required=True, help="comma-separated ZIP list")
    p.add_argument("--since", required=True, help="ISO date, e.g. 2026-05-01")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--demote", action="store_true",
                   help="reverse: residential_approved -> captured")
    return p


if __name__ == "__main__":
    args = _parser().parse_args()
    run(
        state=args.state,
        zips=[z.strip() for z in args.zips.split(",")],
        since=args.since,
        dry_run=args.dry_run,
        demote=args.demote,
    )
