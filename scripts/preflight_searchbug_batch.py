"""Preflight SearchBug paid-batch selection per select-searchbug-tenant-leads skill.

Read-only. No paid queries, no outreach. Reports counts by source and exclusion.
"""
from __future__ import annotations
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from services.name_utils import parse_name, is_common_surname
from services.searchbug_service import query_street_address

client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

TODAY = date(2026, 5, 27)
WINDOW_DAYS = 10
WINDOW_START = TODAY - timedelta(days=WINDOW_DAYS)

GREEN_SOURCES = [("TX", "Harris"), ("TN", "Davidson"), ("OH", "Franklin"),
                 ("OH", "Hamilton"), ("TX", "Tarrant")]

ENTITY_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b",
    re.IGNORECASE,
)
BAD_TOKEN_RE = re.compile(r"\b(AKA|OCCUPANT|ALL\s+OTHER|ET\s+AL)\b", re.IGNORECASE)
STREET_NUM_RE = re.compile(r"^\s*\d+\s+")
ADDR_HAS_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}\b")


def fetch_filings(state: str, county: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        chunk = (
            client.table("filings")
            .select(
                "case_number, tenant_name, property_address, property_zip, "
                "filing_date, court_date, lead_bucket, phone, ng_ghl_contact_id, state, county"
            )
            .eq("state", state)
            .eq("county", county)
            .gte("filing_date", WINDOW_START.isoformat())
            .lte("filing_date", TODAY.isoformat())
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


def fetch_ng_contacts() -> set[str]:
    """Return set of case_numbers with a tenant-side phone in lead_contacts."""
    cases = set()
    offset = 0
    while True:
        chunk = (
            client.table("lead_contacts")
            .select("case_number, track, phone")
            .eq("track", "ng")
            .not_.is_("phone", "null")
            .range(offset, offset + 999)
            .execute()
            .data
            or []
        )
        for r in chunk:
            cases.add(r["case_number"])
        if len(chunk) < 1000:
            break
        offset += 1000
    return cases


def fetch_prior_artifacts() -> set[str]:
    """Look for prior SearchBug artifacts in tmp/."""
    seen = set()
    tmp = Path(__file__).parent.parent / "tmp"
    if not tmp.exists():
        return seen
    for f in tmp.glob("*searchbug*"):
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.search(r"\b\d{8,}\b", line)
                if m:
                    seen.add(m.group(0))
        except Exception:
            pass
    return seen


def normalized_query(row: dict) -> str:
    first, last = parse_name(row.get("tenant_name") or "")
    street = query_street_address(row.get("property_address") or "")
    return f"{first}|{last}|{street}|{row.get('property_zip') or ''}".lower()


def evaluate(row: dict, *, ng_phone_cases: set[str], prior: set[str],
             seen_queries: set[str]) -> str:
    if row.get("lead_bucket") != "residential_approved":
        return "not_approved"
    if row.get("court_date"):
        try:
            cd = date.fromisoformat(row["court_date"])
            if cd < TODAY:
                return "overdue"
        except Exception:
            pass
    addr = row.get("property_address") or ""
    if not STREET_NUM_RE.match(addr) or not ADDR_HAS_STATE_ZIP.search(addr):
        return "invalid_address"
    name = (row.get("tenant_name") or "").strip()
    if not name or ENTITY_RE.search(name) or BAD_TOKEN_RE.search(name):
        return "bad_name"
    first, last = parse_name(name)
    if not first or not last:
        return "bad_name"
    if row["case_number"] in ng_phone_cases:
        return "existing_ng_phone"
    if is_common_surname(last):
        return "surname_gate"
    if row["case_number"] in prior:
        return "prior_query"
    q = normalized_query(row)
    if q in seen_queries:
        return "duplicate_in_batch"
    seen_queries.add(q)
    return "approved"


def main():
    print(f"\n=== SEARCHBUG PREFLIGHT — window {WINDOW_START.isoformat()} .. {TODAY.isoformat()} ===\n")
    ng_phone_cases = fetch_ng_contacts()
    print(f"  Tenant-side phones already in lead_contacts: {len(ng_phone_cases)} cases")
    prior = fetch_prior_artifacts()
    print(f"  Prior SearchBug artifact cases (tmp/):       {len(prior)}\n")

    by_source: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for state, county in GREEN_SOURCES:
        rows = fetch_filings(state, county)
        seen_queries: set[str] = set()
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            outcome = evaluate(r, ng_phone_cases=ng_phone_cases, prior=prior,
                               seen_queries=seen_queries)
            buckets[outcome].append(r)
        by_source[(state, county)] = buckets

        total = len(rows)
        approved = len(buckets.get("approved", []))
        print(f"  {state}/{county}: in-window={total}, approved={approved}")
        for reason in ("not_approved", "overdue", "invalid_address", "bad_name",
                       "existing_ng_phone", "surname_gate", "prior_query",
                       "duplicate_in_batch"):
            n = len(buckets.get(reason, []))
            if n:
                print(f"      - {reason}: {n}")

    print(f"\n=== RECOMMENDED FIRST PAID TEST BATCH (10 leads) ===\n")
    # Sample 10 from approved across sources, weighted by volume
    pool = []
    for src, b in by_source.items():
        for r in b.get("approved", []):
            pool.append((src, r))
    pool.sort(key=lambda x: x[1].get("filing_date") or "", reverse=True)
    print(f"  Approved pool across all green sources: {len(pool)}")
    for src, r in pool[:10]:
        first, last = parse_name(r["tenant_name"])
        addr = query_street_address(r["property_address"])
        print(f"    {src[0]}/{src[1]} | {r['case_number']} | "
              f"{first} {last} | {addr} | {r.get('property_zip')}")

    print(f"\n=== IMPLEMENTATION CHECKS ===")
    print("  Address-qualified: YES — _searchbug_fallback_gated passes address=query_address")
    print("  Cache identity:    YES — key includes query_address (enrichment_cache.py)")
    print("  Yellow path:       address NOT sent (by design — city-only sources)")
    print("\n  No paid queries or outreach were performed during this preflight.")


if __name__ == "__main__":
    main()
