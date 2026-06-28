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

_COLS = "case_number,lead_bucket,tenant_name,property_address,is_enrichable"


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
    """Compute & persist is_enrichable, writing ONLY rows whose value changed.

    Returns {'true': set-to-true, 'false': set-to-false, 'unchanged': n, 'total': n}.

    The default (no args) does a full re-evaluation of every filing but only writes
    the rows whose computed value differs from what's stored. This is the daily
    chain's mode: one cheap paginated read, minimal writes, and — crucially — it
    SELF-HEALS. A row that was stamped is_enrichable=FALSE under old data (e.g. a
    raw-push insert before lead_bucket was set, or any later reclassification) is
    re-checked every run and flips as soon as its inputs justify it. The old
    only_null path could never re-check an already-stamped row, so a stuck FALSE
    stayed stuck until a manual full backfill.

    case_numbers: restrict to these (e.g. a scraper's fresh rows). None = all filings.
    only_null:    legacy incremental mode — consider only rows not yet stamped.
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

    # Write-only-the-diff: compare the freshly computed value against what's stored
    # so a daily full scan stays cheap (most rows are immutable after ingest).
    true_c: list[str] = []
    false_c: list[str] = []
    unchanged = 0
    for r in rows:
        desired = _is_enrichable(r)
        if r.get("is_enrichable") is desired:
            unchanged += 1
        elif desired:
            true_c.append(r["case_number"])
        else:
            false_c.append(r["case_number"])
    _bulk_set(sb, true_c, True, now)
    _bulk_set(sb, false_c, False, now)
    return {"true": len(true_c), "false": len(false_c),
            "unchanged": unchanged, "total": len(rows)}


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
    print(f"scanned {res['total']} filings: "
          f"set TRUE {res['true']} | set FALSE {res['false']} | "
          f"unchanged {res.get('unchanged', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
