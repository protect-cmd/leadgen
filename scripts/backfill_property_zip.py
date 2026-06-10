"""Backfill filings.property_zip from property_address.

Uses the same ZIP extractor as classification (last 5-digit match in the
address). Enables priority_zips join in good_leads_now. Idempotent.

Requires migration 018_priority_zips.sql applied first.

Usage:
    python scripts/backfill_property_zip.py              # all filings
    python scripts/backfill_property_zip.py --only-null  # only rows missing property_zip
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client
from pipeline.qualification import extract_property_zip


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only-null", action="store_true")
    a = ap.parse_args(argv)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    rows, off = [], 0
    while True:
        q = sb.table("filings").select("case_number,property_address,property_zip")
        if a.only_null:
            q = q.is_("property_zip", "null")
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000

    # Group case_numbers by the ZIP we want to set -> chunked bulk updates.
    by_zip: dict[str | None, list[str]] = defaultdict(list)
    changed = 0
    for r in rows:
        z = extract_property_zip(r.get("property_address") or "")
        if z != r.get("property_zip"):
            by_zip[z].append(r["case_number"])
            changed += 1

    for z, cases in by_zip.items():
        if z is None:
            continue
        for i in range(0, len(cases), 200):
            (sb.table("filings").update({"property_zip": z})
             .in_("case_number", cases[i:i + 200]).execute())

    found = sum(len(c) for z, c in by_zip.items() if z is not None)
    print(f"scanned {len(rows)} filings | property_zip set/updated {changed} "
          f"(with ZIP {found}, no ZIP {changed - found})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
