"""Re-classify ALL filings under the Phase-1 qualification rules.

Phase 1 dropped the ZIP allowlist, the rent gate, and the 7-day held bucket.
This re-runs classify_lead over every filing so the ~5,400 leads previously
parked in discarded/held/captured rejoin residential_approved.

Buckets after this run: residential_approved | commercial | discarded(missing_zip).
Then run scripts/flag_enrichable.py to refresh is_enrichable.

Usage:
    python scripts/reclassify_all.py --dry-run
    python scripts/reclassify_all.py --yes-write
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client
from pipeline.qualification import classify_lead


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes-write", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.yes_write:
        ap.error("Pass --dry-run or --yes-write")

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    today = date.today()

    rows, off = [], 0
    while True:
        b = (sb.table("filings").select(
            "case_number,state,property_address,property_type,filing_date,lead_bucket")
            .range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000

    before = Counter(r.get("lead_bucket") or "null" for r in rows)
    # group case_numbers by (new_bucket, notes) for bulk update
    updates: dict[tuple[str, str], list[str]] = {}
    after = Counter()
    changed = 0
    for r in rows:
        try:
            fd = date.fromisoformat(r["filing_date"]) if r.get("filing_date") else today
        except ValueError:
            fd = today
        out = classify_lead(state=r["state"], property_address=r.get("property_address") or "",
                            filing_date=fd, property_type=r.get("property_type"), today=today)
        after[out.lead_bucket] += 1
        if out.lead_bucket != (r.get("lead_bucket") or None):
            changed += 1
        updates.setdefault((out.lead_bucket, out.qualification_notes), []).append(r["case_number"])

    print(f"filings: {len(rows)} | would change bucket: {changed}")
    print(f"  before: {dict(before.most_common())}")
    print(f"  after:  {dict(after.most_common())}")
    if a.dry_run:
        print("\n[DRY RUN] no writes.")
        return 0

    for (bucket, notes), cases in updates.items():
        for i in range(0, len(cases), 200):
            (sb.table("filings").update({"lead_bucket": bucket, "qualification_notes": notes})
             .in_("case_number", cases[i:i + 200]).execute())
    print(f"\nwrote {len(rows)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
