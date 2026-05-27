"""One-shot pre-ship check: how many currently-approved historical rows
would fail the stricter 9-gate address regex? If >5%, the regex must
relax before Phase 2.1 ships.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

STREET_NUM_RE = re.compile(r"^\s*\d+\s+")
ADDR_HAS_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}\b")

client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def main() -> int:
    rows: list[dict] = []
    offset = 0
    while True:
        chunk = (
            client.table("filings")
            .select("case_number, state, property_address")
            .eq("lead_bucket", "residential_approved")
            .range(offset, offset + 999)
            .execute()
            .data or []
        )
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000

    total = len(rows)
    fails = [
        r for r in rows
        if not (STREET_NUM_RE.match(r["property_address"] or "")
                and ADDR_HAS_STATE_ZIP.search(r["property_address"] or ""))
    ]
    pct = (len(fails) / total * 100) if total else 0
    print(f"Approved rows total: {total}")
    print(f"Would fail stricter regex: {len(fails)} ({pct:.1f}%)")
    if pct > 5:
        print("FAIL: regression budget exceeded. Relax regex before shipping Phase 2.1.")
        print("Sample failures:")
        for r in fails[:10]:
            print(f"  {r['state']} | {r['property_address']!r}")
        return 1
    print("PASS: regression budget within bounds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
