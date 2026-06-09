"""Populate filings.is_enrichable — the STATIC half of the good-lead gates.

    is_enrichable = lead_bucket == 'residential_approved'
                    AND gate_name(tenant_name)        # clean person, non-entity
                    AND gate_address(property_address) # street# + STATE ZIP

These inputs are immutable after ingest, so this is computed once per filing
(idempotent; re-runs are safe). The time-varying gates (filing freshness, court
date, not-yet-phoned) live in the good_leads_now SQL view — not here.

Requires migration 017_lead_quality.sql to be applied first.

Usage:
    python scripts/flag_enrichable.py              # backfill ALL filings
    python scripts/flag_enrichable.py --only-null  # only rows not yet flagged
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
from pipeline.gates import gate_name, gate_address

_COLS = "case_number,lead_bucket,tenant_name,property_address"


def _client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def _is_enrichable(f: dict) -> bool:
    return (
        f.get("lead_bucket") == "residential_approved"
        and gate_name(f.get("tenant_name") or "")
        and gate_address(f.get("property_address") or "")
    )


def _bulk_set(sb, cases: list[str], value: bool, now: str) -> None:
    for i in range(0, len(cases), 200):
        (sb.table("filings")
         .update({"is_enrichable": value, "enrichable_checked_at": now})
         .in_("case_number", cases[i:i + 200]).execute())


def flag(case_numbers: list[str] | None = None, only_null: bool = False) -> dict:
    """Compute & persist is_enrichable. Returns {'true': n, 'false': n, 'total': n}.

    case_numbers: restrict to these (e.g. a scraper's fresh rows). None = all filings.
    only_null:    skip rows already flagged (incremental refresh).
    """
    sb = _client()
    now = datetime.now(timezone.utc).isoformat()

    rows: list[dict] = []
    if case_numbers is not None:
        for i in range(0, len(case_numbers), 200):
            rows += (sb.table("filings").select(_COLS)
                     .in_("case_number", case_numbers[i:i + 200]).execute().data or [])
    else:
        off = 0
        while True:
            q = sb.table("filings").select(_COLS)
            if only_null:
                q = q.is_("is_enrichable", "null")
            b = q.range(off, off + 999).execute().data or []
            rows += b
            if len(b) < 1000:
                break
            off += 1000

    true_c = [r["case_number"] for r in rows if _is_enrichable(r)]
    false_c = [r["case_number"] for r in rows if not _is_enrichable(r)]
    _bulk_set(sb, true_c, True, now)
    _bulk_set(sb, false_c, False, now)
    return {"true": len(true_c), "false": len(false_c), "total": len(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only-null", action="store_true", help="only flag rows not yet set")
    a = ap.parse_args(argv)
    try:
        res = flag(only_null=a.only_null)
    except Exception as e:
        msg = repr(e)
        if "is_enrichable" in msg or "column" in msg.lower():
            print("ERROR: column missing — apply migrations/017_lead_quality.sql first.", file=sys.stderr)
            return 2
        raise
    print(f"flagged {res['total']} filings: "
          f"is_enrichable=TRUE {res['true']} | FALSE {res['false']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
