"""Read-only: top-N not-yet-attempted, ranked candidates per business.

No SearchBug, no GHL, no Bland, no DB writes. Reuses the production queue
builders (Vantage/ISTS) and the same selection filters + scoring profiles
(Cosner/Garnish Proof) so the ranking matches what enrichment would pick.

    set -a && source .env && set +a
    python scripts/select_top_candidates.py [--count 125]
"""
from __future__ import annotations

import argparse
import csv
import os
from datetime import date, timedelta

from supabase import create_client

from pipeline.lead_score import score_lead, _PROFILES
from pipeline.queue_builder import _suppress_ists, _SELECT, ISTS_WINDOW_DAYS
from pipeline.qualification import extract_property_zip

OUT_DIR = "outputs"
CD_FRESHNESS_DAYS = 30
GP_FRESHNESS_DAYS = 30

# Operator preference (2026-06-29): rank score-only (NO priority-ZIP tier), and for
# VDG make the date push hard — stale eviction filings = tenant may already be gone.
# Registered at runtime so production pipeline/lead_score.py stays untouched.
VANTAGE_WINDOW_DAYS = 14  # tightened from 21
_PROFILES["vantage_fresh"] = dict(w_rent=40, w_match=20, w_fresh=40,
                                  rent_floor=800.0, rent_cap=3500.0)


def _client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def _fetch_all(q_factory):
    rows, off = [], 0
    while True:
        b = q_factory(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def select_vantage(sb, today: date) -> list[dict]:
    """good_leads_now (not-yet-enriched, gated, fresh), tightened to 14d, scored with
    freshness-heavy weights, sorted by SCORE ONLY (no priority-ZIP tier)."""
    cutoff = (today - timedelta(days=VANTAGE_WINDOW_DAYS)).isoformat()
    rows = _fetch_all(lambda lo, hi: sb.table("good_leads_now").select(_SELECT)
                      .gte("filing_date", cutoff).order("case_number").range(lo, hi))
    rows = _suppress_ists(sb, rows)  # ISTS wins cross-track
    for r in rows:
        ld = date.fromisoformat(r["filing_date"]) if r.get("filing_date") else None
        r["score"] = score_lead(rent=r.get("estimated_rent"), tenant_name=r.get("tenant_name") or "",
                                 lead_date=ld, today=today, fresh_window_days=VANTAGE_WINDOW_DAYS,
                                 profile="vantage_fresh")
    rows.sort(key=lambda r: (-r["score"], r.get("filing_date") or ""), reverse=False)
    rows.sort(key=lambda r: (-r["score"], [-ord(c) for c in (r.get("filing_date") or "")]))
    return rows


def select_ists(sb, today: date) -> list[dict]:
    """ISTS judgments not-yet-enriched within 14d window, scored on ists profile,
    sorted by SCORE ONLY (no priority-ZIP tier)."""
    fresh = (today - timedelta(days=ISTS_WINDOW_DAYS)).isoformat()
    rows = _fetch_all(lambda lo, hi: sb.table("ists_judgments")
                      .select("case_number,defendant_name,property_address,state,county,"
                              "judgment_date,estimated_rent")
                      .is_("phone", "null").gte("judgment_date", fresh)
                      .order("case_number").range(lo, hi))
    for r in rows:
        ld = date.fromisoformat(r["judgment_date"]) if r.get("judgment_date") else None
        r["score"] = score_lead(rent=r.get("estimated_rent"), tenant_name=r.get("defendant_name") or "",
                                 lead_date=ld, today=today, fresh_window_days=ISTS_WINDOW_DAYS,
                                 profile="ists")
    rows.sort(key=lambda r: (-r["score"], [-ord(c) for c in (r.get("judgment_date") or "")]))
    return rows


def select_cosner(sb, today: date) -> list[dict]:
    cutoff = (today - timedelta(days=CD_FRESHNESS_DAYS)).isoformat()
    rows = _fetch_all(lambda lo, hi: sb.table("cosner_filings")
                      .select("case_number,defendant_name,defendant_address,state,county,"
                              "filing_date,answer_deadline,debt_amount")
                      .is_("phone", "null").is_("enriched_at", "null")
                      .gte("filing_date", cutoff).order("case_number").range(lo, hi))
    for r in rows:
        ld = date.fromisoformat(r["filing_date"]) if r.get("filing_date") else None
        r["score"] = score_lead(rent=r.get("debt_amount"), tenant_name=r.get("defendant_name") or "",
                                 lead_date=ld, today=today, fresh_window_days=CD_FRESHNESS_DAYS,
                                 profile="cosner")
    rows.sort(key=lambda r: (-r["score"], -(r.get("debt_amount") or 0)))
    return rows


def select_gp(sb, today: date) -> list[dict]:
    cutoff = (today - timedelta(days=GP_FRESHNESS_DAYS)).isoformat()
    rows = _fetch_all(lambda lo, hi: sb.table("garnishment_orders")
                      .select("case_number,debtor_name,debtor_address,state,county,"
                              "filing_date,exemption_deadline")
                      .is_("phone", "null").is_("enriched_at", "null")
                      .gte("filing_date", cutoff).order("case_number").range(lo, hi))
    for r in rows:
        ld = date.fromisoformat(r["filing_date"]) if r.get("filing_date") else None
        r["score"] = score_lead(rent=None, tenant_name=r.get("debtor_name") or "",
                                 lead_date=ld, today=today, fresh_window_days=GP_FRESHNESS_DAYS,
                                 profile="garnish_proof")
    rows.sort(key=lambda r: (-r["score"], r.get("filing_date") or ""), reverse=False)
    rows.sort(key=lambda r: (-r["score"], r.get("filing_date") or ""))
    return rows


def write_csv(name: str, rows: list[dict], cols: list[str]) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUT_DIR, f"top_candidates_{name}_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def summarize(name: str, rows: list[dict], score_key="score"):
    if not rows:
        print(f"\n=== {name} ===\n  (no not-yet-attempted candidates in window)")
        return
    scores = [r[score_key] for r in rows]
    counties = {}
    for r in rows:
        c = r.get("county") or "?"
        counties[c] = counties.get(c, 0) + 1
    mix = ", ".join(f"{k}:{v}" for k, v in sorted(counties.items(), key=lambda x: -x[1])[:6])
    print(f"\n=== {name} ===")
    print(f"  selected: {len(rows)}   score range: {min(scores)}–{max(scores)}")
    print(f"  county mix: {mix}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=125)
    args = ap.parse_args()
    n = args.count
    sb = _client()
    today = date.today()

    vantage = select_vantage(sb, today)[:n]
    ists = select_ists(sb, today)[:n]
    cosner = select_cosner(sb, today)[:n]
    gp = select_gp(sb, today)[:n]

    summarize("Vantage / VDG (eviction x filed)", vantage)
    p1 = write_csv("vantage", vantage,
                   ["score", "case_number", "tenant_name",
                    "property_address", "property_zip", "state", "county", "filing_date",
                    "court_date", "estimated_rent"])

    summarize("ISTS (eviction x judgment)", ists)
    p2 = write_csv("ists", ists,
                   ["score", "case_number", "defendant_name",
                    "property_address", "state", "county", "judgment_date", "estimated_rent"])

    summarize("Cosner Drake (debt x filed)", cosner)
    p3 = write_csv("cosner", cosner,
                   ["score", "case_number", "defendant_name", "defendant_address", "state",
                    "county", "filing_date", "answer_deadline", "debt_amount"])

    summarize("Garnish Proof (debt x judgment)", gp)
    p4 = write_csv("garnish_proof", gp,
                   ["score", "case_number", "debtor_name", "debtor_address", "state",
                    "county", "filing_date", "exemption_deadline"])

    print("\nCSVs written:")
    for p in (p1, p2, p3, p4):
        print(f"  {p}")


if __name__ == "__main__":
    main()
