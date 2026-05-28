"""Investigate today's Davidson run: what did SearchBug actually return?"""
from __future__ import annotations
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    from services.dedup_service import _client

    today = date.today().isoformat()

    print(f"=== run_metrics today (TN/Davidson) ===")
    rows = (
        _client.table("run_metrics")
        .select("*")
        .eq("state", "TN")
        .eq("county", "Davidson County")
        .gte("run_at", today)
        .order("run_at", desc=True)
        .limit(5)
        .execute()
        .data
        or []
    )
    if not rows:
        print("(no Davidson runs today)")
    for r in rows:
        print(f"\n  run_at: {r['run_at']}")
        for k, v in r.items():
            if k == "run_at": continue
            if v not in (None, 0):
                print(f"    {k}: {v}")

    print(f"\n=== lead_contacts created today for TN ===")
    rows2 = (
        _client.table("lead_contacts")
        .select("case_number,track,phone,searchbug_status,searchbug_returned_name,created_at")
        .gte("created_at", today)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data
        or []
    )
    tn_rows = []
    for r in rows2:
        cn = r.get("case_number", "")
        if "GT" in cn:
            tn_rows.append(r)
    print(f"({len(tn_rows)} TN contacts today)")
    status_counts: Counter[str] = Counter()
    for r in tn_rows:
        status = r.get("searchbug_status") or "(none)"
        phone = "yes" if r.get("phone") else "no"
        status_counts[(status, phone)] += 1
        print(f"  {r['case_number']:18s} track={r['track']:3s} status={status:20s} phone={phone}")

    print("\nStatus x phone breakdown:")
    for (status, phone), n in sorted(status_counts.items()):
        print(f"  {status:25s} phone={phone}: {n}")

    print(f"\n=== filings today for TN/Davidson ===")
    rows3 = (
        _client.table("filings")
        .select("case_number,tenant_name,property_address,filing_date,lead_bucket")
        .eq("state", "TN")
        .eq("county", "Davidson County")
        .gte("created_at", today)
        .limit(10)
        .execute()
        .data
        or []
    )
    print(f"({len(rows3)} sample)")
    for r in rows3:
        print(f"  {r.get('case_number','?'):18s} bucket={r.get('lead_bucket')!s:25s} {r.get('tenant_name','')[:30]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
