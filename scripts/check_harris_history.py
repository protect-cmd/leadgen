"""Quick check: when did Harris last produce filings? Reads run_metrics
to confirm whether the scraper has been silently failing in cron."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    from services.dedup_service import _client

    print("=== run_metrics for Harris ===")
    rows = (
        _client.table("run_metrics")
        .select("run_at,filings_received,phones_found,ghl_created,elapsed_seconds")
        .eq("state", "TX")
        .eq("county", "Harris")
        .order("run_at", desc=True)
        .limit(15)
        .execute()
        .data
        or []
    )
    if not rows:
        print("(no rows)")
    else:
        print(f"{'RUN AT':25s} {'FILINGS':>8s} {'PHONES':>7s} {'GHL':>5s} {'ELAPSED':>9s}")
        for r in rows:
            print(
                f"{r['run_at'][:19]:25s} "
                f"{r.get('filings_received', 0):>8d} "
                f"{r.get('phones_found', 0):>7d} "
                f"{r.get('ghl_created', 0):>5d} "
                f"{(r.get('elapsed_seconds') or 0):>8.1f}s"
            )

    print("\n=== latest 5 Harris filings (any date) ===")
    rows2 = (
        _client.table("filings")
        .select("case_number,filing_date,tenant_name,property_address,lead_bucket")
        .eq("state", "TX")
        .eq("county", "Harris")
        .order("filing_date", desc=True)
        .limit(5)
        .execute()
        .data
        or []
    )
    for r in rows2:
        print(
            f"  {r['filing_date']}  {r['case_number']:25s} "
            f"bucket={r.get('lead_bucket')!s:25s} "
            f"{r.get('tenant_name', '')[:30]}"
        )

    print(f"\nMost recent filing date: {rows2[0]['filing_date'] if rows2 else '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
