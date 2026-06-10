"""Backfill dnc_status on already-enriched numbers via DNCScrub.

Scrubs every enriched phone (lead_contacts ng + ists_judgments) that has no
dnc_status yet and stores the verdict (callable | dnc | unknown) + dnc_checked_at.
This is what makes the existing pool flow into the To-Fire list correctly.

Requires migration 019_dnc_status.sql applied + DNCSCRUB_LOGIN_ID set.

Usage:
    python scripts/backfill_dnc_status.py            # both tracks
    python scripts/backfill_dnc_status.py --table lead_contacts
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client
from services import dnc_service


def _norm(p):
    d = "".join(c for c in (p or "") if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d if len(d) == 10 else None


def _backfill(sb, table: str, extra_filter) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    rows, off = [], 0
    while True:
        q = sb.table(table).select("case_number,phone").not_.is_("phone", "null").is_("dnc_status", "null")
        q = extra_filter(q)
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    if not rows:
        return {"table": table, "scrubbed": 0, "summary": {}}

    phones = [_norm(r["phone"]) for r in rows]
    verdicts = dnc_service.verdict_many([p for p in phones if p])

    # group case_numbers by verdict for bulk update
    by_status: dict[str, list[str]] = defaultdict(list)
    for r, p in zip(rows, phones):
        v = verdicts.get(p, "unknown") if p else "unknown"
        by_status[v].append(r["case_number"])
    for status, cases in by_status.items():
        for i in range(0, len(cases), 200):
            (sb.table(table).update({"dnc_status": status, "dnc_checked_at": now})
             .in_("case_number", cases[i:i + 200])
             .not_.is_("phone", "null").execute())
    return {"table": table, "scrubbed": len(rows), "summary": {k: len(v) for k, v in by_status.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", choices=["lead_contacts", "ists_judgments", "both"], default="both")
    a = ap.parse_args(argv)
    if not os.getenv("DNCSCRUB_LOGIN_ID"):
        print("DNCSCRUB_LOGIN_ID not set — aborting (would fail-closed everything to dnc).", file=sys.stderr)
        return 2

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    targets = ["lead_contacts", "ists_judgments"] if a.table == "both" else [a.table]
    for t in targets:
        flt = (lambda q: q.eq("track", "ng")) if t == "lead_contacts" else (lambda q: q)
        res = _backfill(sb, t, flt)
        print(f"{res['table']}: scrubbed {res['scrubbed']} | {dict(res['summary'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
