"""Remove stale ISTS judgments from the GHL ISTS subaccount (and the SMS drip they
enrolled in), then clear their ghl_contact_id so DB state is truthful.

push_batch (services/ists_ghl.py) historically had no freshness gate, so the enriched
backlog — including months-old judgments — got pushed to GHL/SMS while the Bland dialer
correctly skipped them. This deletes the stale GHL contacts so they stop receiving SMS.

Default selects March-2026 judgments. Use --before YYYY-MM-DD to purge everything older
than a cutoff instead.

    python scripts/purge_stale_ists_ghl.py                      # dry-run, March-2026
    python scripts/purge_stale_ists_ghl.py --before 2026-06-17  # dry-run, all older
    python scripts/purge_stale_ists_ghl.py --execute            # delete March-2026
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import httpx
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client
from services.ists_ghl import _BASE, _headers  # reuse the ISTS subaccount auth


def _select(sb, before: str | None):
    q = (sb.table("ists_judgments")
         .select("case_number,defendant_name,judgment_date,ghl_contact_id")
         .not_.is_("ghl_contact_id", "null"))
    if before:
        q = q.lt("judgment_date", before)
    else:
        q = q.gte("judgment_date", "2026-03-01").lt("judgment_date", "2026-04-01")
    rows, off = [], 0
    while True:
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def _write_backup(rows: list[dict], scope: str) -> Path:
    out_dir = Path("outputs")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_scope = scope.replace(" ", "_").replace("<", "lt").replace(":", "")
    path = out_dir / f"purge_stale_ists_ghl_{safe_scope}_{stamp}.csv"
    fields = ["case_number", "defendant_name", "judgment_date", "ghl_contact_id"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", help="purge judgments with judgment_date < this ISO date "
                                     "(default: only March 2026)")
    ap.add_argument("--execute", action="store_true", help="actually delete (default dry-run)")
    a = ap.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    rows = _select(sb, a.before)
    scope = a.before and f"judgment_date < {a.before}" or "March 2026 judgments"
    print(f"Stale ISTS in GHL to purge ({scope}): {len(rows)}\n")
    if not rows:
        return 0
    if a.execute:
        backup = _write_backup(rows, scope)
        print(f"Backup manifest written: {backup}\n")

    deleted = cleared = failed = 0
    with httpx.Client(timeout=30) as client:
        for r in rows:
            cid = r["ghl_contact_id"]
            line = f"  {r['case_number']:16} {(r.get('defendant_name') or '')[:24]:24} {r.get('judgment_date')}  ghl={cid}"
            if not a.execute:
                print(line + "  [dry-run]")
                continue
            try:
                resp = client.delete(f"{_BASE}/contacts/{cid}", headers=_headers())
                # 200 = deleted; 404 = already gone (still safe to clear our pointer)
                if resp.status_code in (200, 201, 204, 404):
                    deleted += 1 if resp.status_code != 404 else 0
                    sb.table("ists_judgments").update(
                        {"ghl_contact_id": None, "ghl_pushed_at": None}
                    ).eq("case_number", r["case_number"]).execute()
                    cleared += 1
                    print(line + f"  -> deleted({resp.status_code}) + cleared")
                else:
                    failed += 1
                    print(line + f"  GHL DELETE FAILED {resp.status_code}: {resp.text[:120]}")
            except Exception as e:
                failed += 1
                print(line + f"  ERROR: {e!r}")

    if a.execute:
        print(f"\nDONE. GHL contacts deleted: {deleted}, DB pointers cleared: {cleared}, failed: {failed}")
    else:
        print(f"\nDRY-RUN: would purge {len(rows)} GHL contacts. Re-run with --execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
