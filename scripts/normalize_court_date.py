"""Normalize the court_date==filing_date sentinel to NULL.

The Davidson and Hamilton scrapers don't capture a real hearing date — they copy
filing_date into court_date. Once that date passes, the `court_date >= today`
"still actionable" gate (good_leads_now) wrongly drops every such lead. These
counties should behave like Franklin (court_date NULL → governed by filing
freshness). This sets the fake court_date to NULL so the gate stops excluding them.

Idempotent — re-run after each scrape until the Davidson/Hamilton scrapers are
fixed to write NULL directly. PostgREST can't compare column=column, so the
match is done client-side.

Usage:
    python scripts/normalize_court_date.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client


def main() -> int:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    rows, off = [], 0
    while True:
        b = (sb.table("filings").select("case_number,filing_date,court_date")
             .not_.is_("court_date", "null").range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    sentinel = [r["case_number"] for r in rows if r["court_date"] == r["filing_date"]]
    for i in range(0, len(sentinel), 200):
        sb.table("filings").update({"court_date": None}).in_(
            "case_number", sentinel[i:i + 200]).execute()
    print(f"normalized court_date==filing_date -> NULL: {len(sentinel)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
