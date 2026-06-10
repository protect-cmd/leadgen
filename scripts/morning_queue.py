"""Daily enrichment worklist — the priority-ordered queue for SearchBug spend.

Pulls good_leads_now (Vantage: is_enrichable + court-actionable + 21-day fresh +
not-phoned) and orders it the way SearchBug money should be spent:

    priority_rank (ZIP queue: Houston->...->Cincinnati) NULLS LAST,
    then freshest filing first.

This is the single source for "what do we enrich this morning" — replaces the
ad-hoc gate logic scattered across the select_* scripts. Output feeds the
enrich engine. (ISTS good_judgments_now will plug in here once its scraper runs.)

Usage:
    python scripts/morning_queue.py                 # print top of queue + summary
    python scripts/morning_queue.py --limit 100 --csv outputs/queue_today.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client

FIELDS = ["priority_rank", "priority_metro", "filing_date", "case_number",
          "tenant_name", "property_address", "state", "county", "court_date"]


def fetch_queue(sb) -> list[dict]:
    rows, off = [], 0
    while True:
        b = (sb.table("good_leads_now")
             .select("case_number,tenant_name,property_address,state,county,"
                     "filing_date,court_date,priority_rank,priority_metro")
             .range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    # priority_rank asc (NULLS last), then freshest filing first
    rows.sort(key=lambda r: (r["priority_rank"] is None, r["priority_rank"] or 0,
                             _neg(r["filing_date"])))
    return rows


def _neg(d: str | None):
    # sort filing_date descending via reversed codepoints
    return [-ord(c) for c in (d or "")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20, help="rows to print")
    ap.add_argument("--csv", default="", help="write full ordered queue to this path")
    a = ap.parse_args(argv)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    rows = fetch_queue(sb)

    prio = [r for r in rows if r["priority_rank"] is not None]
    print(f"Morning queue (good_leads_now): {len(rows)} leads")
    print(f"  priority-ZIP leads (enrich first): {len(prio)}")
    print(f"  priority tiers: {dict(Counter((r['priority_rank'], r['priority_metro']) for r in prio).most_common())}")
    print(f"  by county: {dict(Counter(r['county'] for r in rows).most_common())}\n")
    print(f"Top {min(a.limit, len(rows))}:")
    for r in rows[:a.limit]:
        tier = f"#{r['priority_rank']} {r['priority_metro']}" if r["priority_rank"] else "  (rent tail)"
        print(f"  {tier:18} {r['filing_date']} {(r['tenant_name'] or '')[:24]:24} {r['county']}")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {len(rows)} -> {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
