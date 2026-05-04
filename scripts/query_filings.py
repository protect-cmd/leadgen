"""
Usage:
  python scripts/query_filings.py            # last 10 rows
  python scripts/query_filings.py 25         # last N rows
  python scripts/query_filings.py 10 enriched  # only rows that have phone or email
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
from supabase import create_client

client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
mode = sys.argv[2] if len(sys.argv) > 2 else "all"

q = (
    client.table("filings")
    .select(
        "case_number, state, county, filing_date, "
        "property_type, estimated_rent, "
        "phone, email, secondary_address, "
        "routing_outcome, ghl_contact_id, bland_triggered, "
        "scraped_at"
    )
    .order("scraped_at", desc=True)
    .limit(limit)
)

if mode == "enriched":
    q = q.not_.is_("phone", "null")

rows = q.execute().data

if not rows:
    print("No rows found.")
    sys.exit(0)

COLS = [
    ("case_number",    14),
    ("state",           5),
    ("county",         10),
    ("filing_date",    12),
    ("property_type",  12),
    ("estimated_rent", 14),
    ("phone",          14),
    ("email",          26),
    ("routing_outcome",16),
    ("ghl_contact_id", 10),
    ("bland_triggered",14),
]

header = "  ".join(name.ljust(w) for name, w in COLS)
sep    = "  ".join("-" * w for _, w in COLS)
print(header)
print(sep)

for r in rows:
    def fmt(key, w):
        v = r.get(key)
        if v is None:
            v = ""
        elif isinstance(v, bool):
            v = str(v)
        else:
            v = str(v)
        return v[:w].ljust(w)

    print("  ".join(fmt(k, w) for k, w in COLS))

print(f"\n{len(rows)} row(s)")
