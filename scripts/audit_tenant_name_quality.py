"""Audit tenant-name quality from green scrapers (Harris first).

For each state/county, sample rows and check:
- name parseability (can we get first + last?)
- multi-tenant signals we may be missing
- compound-surname risk
- business / placeholder noise
- occupant-trailer leftover
- address parseability for SearchBug
"""
from __future__ import annotations
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from services.name_utils import parse_name, split_tenants, is_common_surname

client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def section(t): print(f"\n{'='*70}\n{t}\n{'='*70}")


def fetch_state_county(state: str, county: str | None = None) -> list[dict]:
    rows, offset = [], 0
    while True:
        q = client.table("filings").select(
            "case_number, tenant_name, property_address, lead_bucket, "
            "discard_reason, property_zip, estimated_rent, phone, "
            "ng_ghl_contact_id, state, county"
        ).eq("state", state)
        if county:
            q = q.eq("county", county)
        chunk = q.range(offset, offset + 999).execute().data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += 1000
    return rows


BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"\b(JOHN\s+DOE|JANE\s+DOE|ALL\s+OCCUPANTS?|UNKNOWN\s+TENANT|"
    r"TENANT\s+IN\s+POSSESSION|OCCUPANTS?\s+UNKNOWN|UNKNOWN)\b",
    re.IGNORECASE,
)
COMPOUND_PARTICLES = re.compile(
    r"\b(DE\s+LA|DEL|DE\s+LOS|DE\s+|VAN\s+DER|VAN\s+|VON\s+|DA\s+|LA\s+|LOS\s+)",
    re.IGNORECASE,
)
CONJUNCTION_RE = re.compile(r"\b(AND|&)\b", re.IGNORECASE)
TRAILER_RE = re.compile(
    r"(AND\s+ALL\s+OTHER\s+OCCUPANTS?|ET\s+AL|AND\s+ALL\s+OCCUPANTS?)",
    re.IGNORECASE,
)


def audit_county(rows: list[dict], label: str) -> None:
    section(f"{label}  (n={len(rows)})")
    if not rows:
        return

    total = len(rows)
    blank = sum(1 for r in rows if not (r.get("tenant_name") or "").strip())
    placeholder = sum(1 for r in rows if PLACEHOLDER_RE.search(r.get("tenant_name") or ""))
    business = sum(1 for r in rows if BUSINESS_RE.search(r.get("tenant_name") or ""))
    trailer = sum(1 for r in rows if TRAILER_RE.search(r.get("tenant_name") or ""))
    conjunction = sum(1 for r in rows if CONJUNCTION_RE.search(r.get("tenant_name") or ""))
    compound = sum(1 for r in rows if COMPOUND_PARTICLES.search(r.get("tenant_name") or ""))

    parse_ok = 0
    parse_fail = 0
    common_surname = 0
    split_multi = 0
    for r in rows:
        name = (r.get("tenant_name") or "").strip()
        if not name:
            continue
        segs = split_tenants(name)
        if len(segs) > 1:
            split_multi += 1
        first, last = parse_name(segs[0])
        if first and last:
            parse_ok += 1
            if is_common_surname(last):
                common_surname += 1
        else:
            parse_fail += 1

    print(f"  total rows:                {total}")
    print(f"  blank tenant_name:         {blank}")
    print(f"  placeholder names:         {placeholder}  ({placeholder/total*100:.1f}%)")
    print(f"  business-name flagged:     {business}  ({business/total*100:.1f}%)")
    print(f"  contains occupant trailer: {trailer}  ({trailer/total*100:.1f}%)")
    print(f"  contains AND/&:            {conjunction}  ({conjunction/total*100:.1f}%)")
    print(f"  contains compound particle: {compound}  ({compound/total*100:.1f}%)")
    print(f"  split_tenants > 1 segment: {split_multi}  ({split_multi/total*100:.1f}%)")
    print(f"  parse_name OK:             {parse_ok}  ({parse_ok/total*100:.1f}%)")
    print(f"  parse_name FAIL:           {parse_fail}  ({parse_fail/total*100:.1f}%)")
    print(f"  common-surname hit:        {common_surname}  ({common_surname/total*100:.1f}%)")

    # Sample failure modes
    print("\n  -- sample BUSINESS-flagged tenant names --")
    for r in rows:
        if BUSINESS_RE.search(r.get("tenant_name") or ""):
            print(f"    {r['tenant_name']!r}")
            if sum(1 for _ in rows if BUSINESS_RE.search(_.get('tenant_name') or '')) > 8: break
    biz_samples = [r['tenant_name'] for r in rows if BUSINESS_RE.search(r.get('tenant_name') or '')][:8]
    print("\n  -- sample PLACEHOLDER tenant names --")
    for n in [r['tenant_name'] for r in rows if PLACEHOLDER_RE.search(r.get('tenant_name') or '')][:8]:
        print(f"    {n!r}")
    print("\n  -- sample TRAILER (occupants) leftover --")
    for n in [r['tenant_name'] for r in rows if TRAILER_RE.search(r.get('tenant_name') or '')][:8]:
        print(f"    {n!r}")
    print("\n  -- sample CONJUNCTION (multi-tenant signal) --")
    for n in [r['tenant_name'] for r in rows if CONJUNCTION_RE.search(r.get('tenant_name') or '')][:8]:
        print(f"    {n!r}")
    print("\n  -- sample COMPOUND-PARTICLE names --")
    for n in [r['tenant_name'] for r in rows if COMPOUND_PARTICLES.search(r.get('tenant_name') or '')][:8]:
        print(f"    {n!r}")
    print("\n  -- sample PARSE FAIL names --")
    fails = []
    for r in rows:
        name = (r.get("tenant_name") or "").strip()
        if not name: continue
        first, last = parse_name(split_tenants(name)[0])
        if not (first and last):
            fails.append(name)
    for n in fails[:10]:
        print(f"    {n!r}")
    print("\n  -- sample COMMON SURNAME (would be skipped by SearchBug filter) --")
    cs = []
    for r in rows:
        name = (r.get("tenant_name") or "").strip()
        if not name: continue
        first, last = parse_name(split_tenants(name)[0])
        if first and last and is_common_surname(last):
            cs.append(name)
    for n in cs[:8]:
        print(f"    {n!r}")

    # Cross with outcomes
    print("\n  -- phone-hit rate by name shape (among enriched rows where SearchBug was attempted) --")
    enrichable = [r for r in rows if r.get("lead_bucket") in ("residential_approved", "held")]
    if enrichable:
        with_phone = sum(1 for r in enrichable if r.get("phone"))
        print(f"     enrichable rows: {len(enrichable)}; phone found: {with_phone} ({with_phone/len(enrichable)*100:.1f}%)")
        common = [r for r in enrichable if r.get('tenant_name') and is_common_surname(parse_name(split_tenants(r['tenant_name'])[0])[1])]
        rare = [r for r in enrichable if r.get('tenant_name') and not is_common_surname(parse_name(split_tenants(r['tenant_name'])[0])[1])]
        if common:
            cp = sum(1 for r in common if r.get('phone'))
            print(f"     common-surname enrichable: {len(common)}; phone: {cp} ({cp/len(common)*100:.1f}%)")
        if rare:
            rp = sum(1 for r in rare if r.get('phone'))
            print(f"     rare-surname enrichable: {len(rare)}; phone: {rp} ({rp/len(rare)*100:.1f}%)")


if __name__ == "__main__":
    # Harris first
    audit_county(fetch_state_county("TX", "Harris"), "TX / Harris (CSV extract)")
    audit_county(fetch_state_county("TX", "Tarrant"), "TX / Tarrant")
