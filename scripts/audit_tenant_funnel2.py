"""Deeper drill: OH unclassified mystery, approved-ZIP profile, NG-side enrichment yield."""
from __future__ import annotations
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def section(t): print(f"\n{'='*70}\n{t}\n{'='*70}")


def fetch_all(table, select):
    rows, offset = [], 0
    while True:
        chunk = client.table(table).select(select).range(offset, offset + 999).execute().data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


rows = fetch_all(
    "filings",
    "state, county, property_address, property_zip, lead_bucket, "
    "discard_reason, estimated_rent, phone, ng_ghl_contact_id, "
    "ghl_contact_id, scraped_at, source_url, classified_at",
)

# 1. OH unclassified - look at addresses
section("OH unclassified — address samples & county breakdown")
oh_null = [r for r in rows if r.get("state") == "OH" and r.get("lead_bucket") is None]
print(f"  total: {len(oh_null)}")
cnt = Counter(r.get("county") for r in oh_null)
print(f"  by county: {dict(cnt)}")
print(f"  any classified_at? {sum(1 for r in oh_null if r.get('classified_at'))}")
for r in oh_null[:8]:
    print(f"    {r.get('county')} | addr={r.get('property_address')!r}")

# OH classified
section("OH classified — addresses & buckets")
oh_cls = [r for r in rows if r.get("state") == "OH" and r.get("lead_bucket") is not None]
print(f"  total: {len(oh_cls)}")
for r in oh_cls[:5]:
    print(f"    {r.get('county')} | bucket={r.get('lead_bucket')} | "
          f"zip={r.get('property_zip')} | phone={'y' if r.get('phone') else 'n'} | "
          f"addr={(r.get('property_address') or '')[:80]}")

# 2. Approved ZIPs (what's working)
section("TOP APPROVED ZIPS — TX")
tx_appr = [r for r in rows if r.get("state") == "TX" and r.get("lead_bucket") == "residential_approved"]
for z, c in Counter(r.get("property_zip") for r in tx_appr).most_common(20):
    print(f"  {z}: {c}")

section("TOP APPROVED ZIPS — TN")
tn_appr = [r for r in rows if r.get("state") == "TN" and r.get("lead_bucket") == "residential_approved"]
for z, c in Counter(r.get("property_zip") for r in tn_appr).most_common(20):
    print(f"  {z}: {c}")

# 3. SearchBug yield in OH — addresses don't have ZIP (yellow source)
section("OH approved addresses (any ZIP?)")
print(f"  approved with zip: {sum(1 for r in oh_cls if r.get('lead_bucket') == 'residential_approved' and r.get('property_zip'))}/{sum(1 for r in oh_cls if r.get('lead_bucket') == 'residential_approved')}")
for r in oh_cls[:5]:
    if r.get("lead_bucket") == "residential_approved":
        print(f"    addr={r.get('property_address')!r} zip={r.get('property_zip')}")

# 4. NG contacts created (ng_ghl_contact_id not null)
section("NG GHL contacts created (real tenant-side wins)")
ng_created = [r for r in rows if r.get("ng_ghl_contact_id")]
print(f"  total NG contacts: {len(ng_created)}")
print(f"  by state: {Counter(r.get('state') for r in ng_created)}")

# 5. Phone hit per discarded-zip category (would we have hit anything?)
section("RENT ESTIMATE COVERAGE among approved (TX/TN/OH)")
for st in ("TX", "TN", "OH"):
    appr = [r for r in rows if r.get("state") == st and r.get("lead_bucket") == "residential_approved"]
    have = sum(1 for r in appr if r.get("estimated_rent") is not None)
    print(f"  {st}: {have}/{len(appr)} approved have estimated_rent")

# 6. Approved leads where rent below current state threshold (would stricter filter kill them?)
section("WHAT WOULD A STRICTER RENT THRESHOLD KILL?")
TX_TH = 1500
TN_TH = 1600
for st, th in (("TX", TX_TH), ("TN", TN_TH)):
    appr = [r for r in rows if r.get("state") == st and r.get("lead_bucket") == "residential_approved"
            and r.get("estimated_rent") is not None]
    for new_th in (th, th + 200, th + 500, th + 800, th + 1200):
        n_killed = sum(1 for r in appr if r["estimated_rent"] < new_th)
        n_kept = len(appr) - n_killed
        print(f"  {st} threshold ${new_th}: kept={n_kept}/{len(appr)} (killed {n_killed})")
