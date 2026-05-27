"""Ad-hoc audit: characterize tenant lead funnel and ZIP-filter impact."""
from __future__ import annotations
import os
import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def fetch_all(table: str, select: str, page_size: int = 1000) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        chunk = (
            client.table(table)
            .select(select)
            .range(offset, offset + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
        offset += page_size
    return rows


def section(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


rows = fetch_all(
    "filings",
    "state, county, property_zip, lead_bucket, discard_reason, "
    "estimated_rent, phone, ng_ghl_contact_id, scraped_at",
)

# 1. Per-state funnel
section("PER-STATE FUNNEL")
by_state: dict[str, list[dict]] = defaultdict(list)
for r in rows:
    by_state[r.get("state") or "?"].append(r)
print(f"  {'state':<5} {'total':>7} {'discarded':>10} {'approved':>10} "
      f"{'pct_zip_kill':>13} {'phone_hit':>10}")
for st, lst in sorted(by_state.items(), key=lambda x: -len(x[1])):
    total = len(lst)
    discarded = sum(1 for r in lst if r.get("lead_bucket") == "discarded")
    zip_kill = sum(1 for r in lst if r.get("discard_reason") == "zip_not_approved")
    approved = sum(1 for r in lst if r.get("lead_bucket") == "residential_approved")
    phones = sum(1 for r in lst if r.get("phone"))
    pct_zip = (zip_kill / total * 100) if total else 0
    print(f"  {st:<5} {total:>7} {discarded:>10} {approved:>10} {pct_zip:>12.1f}% {phones:>10}")

# 2. Top ZIPs we ARE discarding in TX/OH/TN (volume of leads thrown away)
section("TOP 25 DISCARDED ZIPS in TX (volume of leads we throw away)")
tx_disc = [r for r in rows if r.get("state") == "TX" and r.get("discard_reason") == "zip_not_approved"]
tx_zip_counter = Counter(r.get("property_zip") for r in tx_disc)
for z, c in tx_zip_counter.most_common(25):
    print(f"  {z}: {c}")

section("TOP 25 DISCARDED ZIPS in TN")
tn_disc = [r for r in rows if r.get("state") == "TN" and r.get("discard_reason") == "zip_not_approved"]
for z, c in Counter(r.get("property_zip") for r in tn_disc).most_common(25):
    print(f"  {z}: {c}")

section("TOP 25 DISCARDED ZIPS in OH")
oh_disc = [r for r in rows if r.get("state") == "OH" and r.get("discard_reason") == "zip_not_approved"]
for z, c in Counter(r.get("property_zip") for r in oh_disc).most_common(25):
    print(f"  {z}: {c}")

# 3. OH null lead_bucket mystery
section("OH null-lead_bucket rows (sample)")
oh_null = [r for r in rows if r.get("state") == "OH" and r.get("lead_bucket") is None]
print(f"  count: {len(oh_null)}")
for r in oh_null[:5]:
    print(f"  zip={r.get('property_zip')} reason={r.get('discard_reason')} "
          f"phone={'y' if r.get('phone') else 'n'} ng_ghl={'y' if r.get('ng_ghl_contact_id') else 'n'} "
          f"scraped_at={r.get('scraped_at')}")

# 4. Rent distribution among TX-approved
section("ESTIMATED RENT distribution — TX residential_approved")
tx_appr = [r for r in rows if r.get("state") == "TX" and r.get("lead_bucket") == "residential_approved"]
rents = [r["estimated_rent"] for r in tx_appr if r.get("estimated_rent") is not None]
print(f"  approved rows with rent: {len(rents)}/{len(tx_appr)}")
if rents:
    rents = sorted(rents)
    print(f"  min={rents[0]:.0f} p25={rents[len(rents)//4]:.0f} "
          f"median={statistics.median(rents):.0f} "
          f"p75={rents[3*len(rents)//4]:.0f} max={rents[-1]:.0f} "
          f"mean={statistics.mean(rents):.0f}")
    # bucket counts
    buckets = [(0, 1500), (1500, 1800), (1800, 2200), (2200, 2800), (2800, 3500), (3500, 100000)]
    for lo, hi in buckets:
        n = sum(1 for x in rents if lo <= x < hi)
        print(f"    ${lo:>5}-${hi:>5}: {n}")

# 5. Rent_below_threshold distribution — what did we kill?
section("ESTIMATED RENT among TX rent_below_threshold")
tx_low = [r for r in rows if r.get("state") == "TX" and r.get("discard_reason") == "rent_below_threshold"]
rents2 = [r["estimated_rent"] for r in tx_low if r.get("estimated_rent") is not None]
print(f"  rows: {len(rents2)}")
if rents2:
    rents2 = sorted(rents2)
    print(f"  min={rents2[0]:.0f} median={statistics.median(rents2):.0f} max={rents2[-1]:.0f}")

# 6. Phone hit rate among approved
section("PHONE HIT RATE by state (approved only)")
for st, lst in by_state.items():
    appr = [r for r in lst if r.get("lead_bucket") == "residential_approved"]
    phoned = sum(1 for r in appr if r.get("phone"))
    if appr:
        print(f"  {st}: {phoned}/{len(appr)} = {phoned/len(appr)*100:.1f}%")

# 7. Approved leads with no phone — why?
section("APPROVED-BUT-NO-PHONE (per state)")
for st, lst in by_state.items():
    appr = [r for r in lst if r.get("lead_bucket") == "residential_approved" and not r.get("phone")]
    if appr:
        print(f"  {st}: {len(appr)} approved-no-phone")

# 8. Time trend — last 30 days vs prior
section("RECENT 30 DAYS funnel (by state)")
from datetime import datetime, timezone, timedelta
cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
recent = [r for r in rows if (r.get("scraped_at") or "") >= cutoff]
print(f"  recent rows: {len(recent)}")
by_state_r: dict[str, list[dict]] = defaultdict(list)
for r in recent:
    by_state_r[r.get("state") or "?"].append(r)
for st, lst in sorted(by_state_r.items(), key=lambda x: -len(x[1])):
    total = len(lst)
    zip_kill = sum(1 for r in lst if r.get("discard_reason") == "zip_not_approved")
    approved = sum(1 for r in lst if r.get("lead_bucket") == "residential_approved")
    phones = sum(1 for r in lst if r.get("phone"))
    print(f"  {st}: total={total} zip_kill={zip_kill} approved={approved} phones={phones}")
