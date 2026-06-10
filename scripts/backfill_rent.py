"""Backfill filings.estimated_rent from Rentometer (market median) for enrichable
leads missing it — so the dashboard shows a rent estimate per lead.

Usage:
    python scripts/backfill_rent.py            # all is_enrichable missing rent
    python scripts/backfill_rent.py --limit 500
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client

RENTOMETER_KEY = "gOc0kDVnoj6nRkSRlDpQqg"
_CTX = ssl._create_unverified_context()


def rentometer_median(address: str, bedrooms: int = 2):
    q = urllib.parse.urlencode({"api_key": RENTOMETER_KEY, "address": address, "bedrooms": bedrooms})
    try:
        resp = urllib.request.urlopen(
            f"https://www.rentometer.com/api/v1/summary?{q}", timeout=30, context=_CTX)
        d = json.loads(resp.read())
        return None if d.get("error") else d.get("median")
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    rows, off = [], 0
    while True:
        b = (sb.table("filings").select("case_number,property_address")
             .eq("is_enrichable", True).is_("estimated_rent", "null")
             .not_.is_("property_address", "null").range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000 or (a.limit and len(rows) >= a.limit):
            break
        off += 1000
    if a.limit:
        rows = rows[:a.limit]
    print(f"to backfill: {len(rows)}", flush=True)

    done = found = 0
    for r in rows:
        med = rentometer_median(r["property_address"])
        if med:
            sb.table("filings").update({"estimated_rent": med}).eq("case_number", r["case_number"]).execute()
            found += 1
        done += 1
        if done % 100 == 0:
            print(f"  {done}/{len(rows)} | rent found {found}", flush=True)
        time.sleep(0.1)
    print(f"done: {done} processed | {found} rents stored", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
