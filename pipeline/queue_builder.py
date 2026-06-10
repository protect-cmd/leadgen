"""Shared builders for the two operational work lists, both scored + ordered:

  to_enrich — good_leads_now (no phone yet) -> needs a SearchBug query
  to_fire   — enriched + still-actionable + NOT yet dialed -> needs a Bland call

Both order by (priority_rank NULLS LAST, score DESC) so the best SearchBug/Bland
spend floats to the top. Used by scripts/morning_queue.py and the dashboard.
"""
from __future__ import annotations

from datetime import date, timedelta

from pipeline.lead_score import score_lead, compute_coverage_rates
from pipeline.qualification import extract_property_zip

_SELECT = ("case_number,tenant_name,property_address,state,county,"
           "filing_date,court_date,priority_rank,priority_metro")


def _score_and_sort(rows: list[dict], coverage: dict[str, float], today: date) -> list[dict]:
    for r in rows:
        fd = date.fromisoformat(r["filing_date"]) if r.get("filing_date") else None
        r["score"] = score_lead(tenant_name=r.get("tenant_name") or "", filing_date=fd,
                                 county=r.get("county") or "", coverage_rates=coverage, today=today)
    rows.sort(key=lambda r: (r.get("priority_rank") is None, r.get("priority_rank") or 0,
                             -r["score"], [-ord(c) for c in (r.get("filing_date") or "")]))
    return rows


def build_to_enrich(sb, dnc_dir: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    coverage = compute_coverage_rates(sb, dnc_dir)
    rows, off = [], 0
    while True:
        b = sb.table("good_leads_now").select(_SELECT).range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return _score_and_sort(rows, coverage, today)


def _priority_map(sb) -> dict[str, tuple[int, str]]:
    return {p["zip"]: (p["queue_rank"], p["metro"])
            for p in (sb.table("priority_zips").select("zip,queue_rank,metro").execute().data or [])}


def build_ists_to_enrich(sb, dnc_dir: str, today: date | None = None) -> list[dict]:
    """ISTS To-Enrich: tenant-lost judgments (gated at scrape) not yet enriched,
    within the 7-day legal window. Scored on judgment_date (7-day freshness)."""
    today = today or date.today()
    coverage = compute_coverage_rates(sb, dnc_dir)
    fresh = (today - timedelta(days=7)).isoformat()
    pri = _priority_map(sb)

    rows, off = [], 0
    while True:
        b = (sb.table("ists_judgments")
             .select("case_number,defendant_name,property_address,state,county,"
                     "judgment_date,prior_phone")
             .is_("phone", "null").gte("judgment_date", fresh)
             .range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000

    for r in rows:
        r["tenant_name"] = r.get("defendant_name")
        r["filing_date"] = r.get("judgment_date")   # judgment date drives ISTS freshness
        r["court_date"] = None
        z = extract_property_zip(r.get("property_address") or "")
        rank, metro = pri.get(z, (None, None))
        r["priority_rank"], r["priority_metro"] = rank, metro
        fd = date.fromisoformat(r["judgment_date"]) if r.get("judgment_date") else None
        r["score"] = score_lead(tenant_name=r["tenant_name"] or "", filing_date=fd,
                                county=r.get("county") or "", coverage_rates=coverage,
                                today=today, fresh_window_days=7)
    rows.sort(key=lambda r: (r.get("priority_rank") is None, r.get("priority_rank") or 0,
                             -r["score"], [-ord(c) for c in (r.get("judgment_date") or "")]))
    return rows


def build_to_fire(sb, dnc_dir: str, today: date | None = None) -> list[dict]:
    """Enriched (phone present) + still-actionable (is_enrichable + court-future +
    21-day fresh) + not-yet-dialed (bland_call_id IS NULL). Stale/old-court leads
    fall away both via the gates and via low freshness score."""
    today = today or date.today()
    coverage = compute_coverage_rates(sb, dnc_dir)
    fresh = (today - timedelta(days=21)).isoformat()
    today_s = today.isoformat()

    # priority map (priority_rank lives on the view, not filings) — look up by ZIP
    pri = {p["zip"]: (p["queue_rank"], p["metro"])
           for p in (sb.table("priority_zips").select("zip,queue_rank,metro").execute().data or [])}

    # actionable filings (same gates as good_leads_now, minus the not-phoned clause)
    cols = ("case_number,tenant_name,property_address,state,county,"
            "filing_date,court_date,property_zip")
    base, off = [], 0
    while True:
        b = (sb.table("filings").select(cols)
             .eq("is_enrichable", True).gte("filing_date", fresh)
             .range(off, off + 999).execute().data or [])
        base += b
        if len(b) < 1000:
            break
        off += 1000
    base = [r for r in base if not r.get("court_date") or r["court_date"] >= today_s]
    for r in base:
        rank, metro = pri.get(r.get("property_zip"), (None, None))
        r["priority_rank"], r["priority_metro"] = rank, metro

    # join lead_contacts: phone present, not yet dialed
    cns = [r["case_number"] for r in base]
    ready: dict[str, dict] = {}
    for i in range(0, len(cns), 200):
        for lc in (sb.table("lead_contacts").select("case_number,phone,bland_status,ghl_contact_id")
                   .in_("case_number", cns[i:i + 200]).eq("track", "ng")
                   .not_.is_("phone", "null").is_("bland_call_id", "null").execute().data or []):
            ready[lc["case_number"]] = lc
    rows = []
    for r in base:
        lc = ready.get(r["case_number"])
        if not lc:
            continue
        r["phone"] = lc["phone"]
        r["staged"] = bool(lc.get("ghl_contact_id"))
        r["bland_status"] = lc.get("bland_status")
        rows.append(r)
    return _score_and_sort(rows, coverage, today)
