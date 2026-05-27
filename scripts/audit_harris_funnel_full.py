"""Trace the full Harris funnel: 4,489 filings -> N reachable leads. Show
each leak step with counts."""
from __future__ import annotations
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def fetch_all() -> list[dict]:
    rows, offset = [], 0
    while True:
        chunk = (
            client.table("filings")
            .select(
                "case_number, tenant_name, property_address, property_zip, "
                "lead_bucket, discard_reason, property_type, estimated_rent, "
                "phone, email, bland_status, ng_bland_status, "
                "ghl_contact_id, ng_ghl_contact_id, dnc_status, ng_dnc_status, "
                "secondary_address"
            )
            .eq("state", "TX")
            .eq("county", "Harris")
            .range(offset, offset + 999)
            .execute()
            .data
            or []
        )
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


rows = fetch_all()
total = len(rows)
print(f"\n=== HARRIS TX END-TO-END FUNNEL (n={total}) ===\n")

# Step 1: pre-enrichment qualification
discarded = [r for r in rows if r.get("lead_bucket") == "discarded"]
missing_zip = [r for r in discarded if r.get("discard_reason") == "missing_zip"]
zip_killed = [r for r in discarded if r.get("discard_reason") == "zip_not_approved"]

print(f"  Step 1 (qualify by ZIP):")
print(f"    {total:>5} raw filings")
print(f"   -{len(missing_zip):>5} missing_zip (scraper didn't yield ZIP)")
print(f"   -{len(zip_killed):>5} zip_not_approved (ZIP not on allowlist)")
after_zip = [r for r in rows if r.get("lead_bucket") not in (None, "discarded")] + \
            [r for r in rows if r.get("lead_bucket") == "discarded" and r.get("discard_reason") not in ("missing_zip", "zip_not_approved")]
# Use simpler: anything not 'discarded' OR 'discarded' with different reason
passed_qual = [r for r in rows if r.get("lead_bucket") and r.get("lead_bucket") != "discarded"] + \
              [r for r in rows if r.get("lead_bucket") == "discarded" and r.get("discard_reason") not in ("missing_zip", "zip_not_approved")]
print(f"    {len(passed_qual):>5} pass qualification\n")

# Of those that passed qualification, post-enrichment classifications
buckets = Counter(r.get("lead_bucket") for r in passed_qual)
print(f"  Step 2 (post-enrichment lead_bucket):")
for b, c in buckets.most_common():
    print(f"    {c:>5} {b}")

# Step 3: 'reachable' = has any contact (phone OR email)
appr = [r for r in rows if r.get("lead_bucket") in ("residential_approved", "held", "commercial")]
print(f"\n  Step 3 (enrichment outcome among {len(appr)} qualified-and-classified):")
with_phone = [r for r in appr if r.get("phone")]
with_email = [r for r in appr if r.get("email")]
with_either = [r for r in appr if r.get("phone") or r.get("email")]
print(f"    {len(with_phone):>5} have phone  ({len(with_phone)/len(appr)*100:.1f}%)")
print(f"    {len(with_email):>5} have email  ({len(with_email)/len(appr)*100:.1f}%)")
print(f"    {len(with_either):>5} have either ({len(with_either)/len(appr)*100:.1f}%)")
print(f"    {len(appr) - len(with_either):>5} have NEITHER  -- biggest silent leak")

# Step 4: among with-phone, NG (tenant) GHL contact created?
print(f"\n  Step 4 (NG tenant routing among {len(with_phone)} with phone):")
ng_pushed = [r for r in with_phone if r.get("ng_ghl_contact_id")]
ng_dnc_blocked = [r for r in with_phone if (r.get("ng_dnc_status") or "").lower() == "blocked"]
print(f"    {len(ng_pushed):>5} pushed to NG GHL (real tenant leads)")
print(f"    {len(ng_dnc_blocked):>5} blocked by DNC on tenant side")

# Step 5: bland_status breakdown to see explicit skip reasons
print(f"\n  ng_bland_status breakdown across all {total} rows:")
for st, c in Counter(r.get("ng_bland_status") for r in rows).most_common():
    print(f"    {c:>5} {st}")

# Why qualified-but-no-contact? Common diagnostic: did SearchBug have what it needed?
print(f"\n  Diagnostic: among {len(appr)-len(with_either)} qualified-but-no-contact:")
no_contact = [r for r in appr if not (r.get("phone") or r.get("email"))]
print(f"    rows with property_type set:    {sum(1 for r in no_contact if r.get('property_type'))}")
print(f"    rows with estimated_rent set:   {sum(1 for r in no_contact if r.get('estimated_rent'))}")
print(f"    rows with secondary_address:    {sum(1 for r in no_contact if r.get('secondary_address'))}")
print(f"    rows with bland_status=missing_contact_data: "
      f"{sum(1 for r in no_contact if r.get('bland_status') == 'missing_contact_data')}")
print(f"    rows with ng_bland_status=missing_contact_data: "
      f"{sum(1 for r in no_contact if r.get('ng_bland_status') == 'missing_contact_data')}")

# Sample 6 no-contact rows for inspection
print(f"\n  Sample no-contact rows (name | address):")
for r in no_contact[:8]:
    print(f"    {r['tenant_name']!r:<55} | {(r['property_address'] or '')[:70]}")

# Final headline
print(f"\n=== HARRIS HEADLINE ===")
print(f"  Raw filings:         {total}")
print(f"  Passed qualification:{len(appr)}")
print(f"  Got phone:           {len(with_phone)}")
print(f"  Pushed to NG GHL:    {len(ng_pushed)}")
print(f"  Effective conversion:{len(ng_pushed)/total*100:.2f}% of raw")
