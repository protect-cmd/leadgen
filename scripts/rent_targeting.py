"""Refined Rentometer-candidate targeting (pure, testable core + a CLI).

Lesson from the 2026-06-12 batch (35% landed >=$1600 vs ~70% projected): ranking
by ZIP *median* and defaulting priority-only ZIPs high put noisy / unproven ZIPs
at the top. This ranks by the actual predictor — historical %>=$1600 — and only
trusts a ZIP once it has enough samples.

Tiers (best first):
  0  PROVEN  — ZIP has >= MIN_N prior estimates AND >= MIN_PCT of them were >=$1600
  1  PRIORITY— curated priority ZIP without enough history (plausible, unproven)
  (tail ZIPs are dropped — don't spend Rentometer on unproven low-rent areas)

Usage:
    python scripts/rent_targeting.py --cap 250 --out targets.csv
"""
from __future__ import annotations

import statistics
from collections import defaultdict

MIN_N = 8        # a ZIP needs this many prior estimates before we trust its yield
MIN_PCT = 0.65   # ... and at least this share of them >= $1600 to be "proven"
RENT_FLOOR = 1600.0


def zip_yield(estimates, min_n: int = MIN_N) -> dict:
    """estimates: iterable of (zip, rent). Returns {zip: {median, pct, n}} for
    ZIPs with at least `min_n` non-null rents."""
    by: dict[str, list[float]] = defaultdict(list)
    for z, rent in estimates:
        if z and rent:
            by[z].append(float(rent))
    out = {}
    for z, vals in by.items():
        if len(vals) >= min_n:
            out[z] = {
                "median": statistics.median(vals),
                "pct": sum(1 for v in vals if v >= RENT_FLOOR) / len(vals),
                "n": len(vals),
            }
    return out


def _rank_key(cand: dict, yields: dict, priority_zips: set, min_pct: float):
    z = cand.get("property_zip")
    y = yields.get(z)
    score = -int(cand.get("score") or 0)
    recency = [-ord(c) for c in (cand.get("filing_date") or "")]
    if y and y["pct"] >= min_pct:
        return (0, -y["pct"], -y["median"], score, recency)   # PROVEN
    if z in priority_zips:
        return (1, 0.0, 0.0, score, recency)                  # PRIORITY (unproven)
    return (2, 0.0, 0.0, 0.0, [])                             # tail -> dropped


def select_targets(candidates, yields, priority_zips, *, min_pct: float = MIN_PCT, cap: int | None = None):
    """Rank candidates: PROVEN (by %>=1600, then median) first, then PRIORITY-only.
    Drops tail ZIPs entirely. Returns the ranked list (capped)."""
    keyed = [(_rank_key(c, yields, priority_zips, min_pct), c) for c in candidates]
    keyed = [(k, c) for k, c in keyed if k[0] <= 1]
    keyed.sort(key=lambda kc: kc[0])
    ranked = [c for _, c in keyed]
    return ranked[:cap] if cap else ranked


def tier_of(cand: dict, yields: dict, priority_zips: set, min_pct: float = MIN_PCT) -> str:
    return {0: "proven", 1: "priority", 2: "tail"}[_rank_key(cand, yields, priority_zips, min_pct)[0]]


def _main(argv=None) -> int:
    import argparse
    import csv
    import os
    import sys
    from datetime import date
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client
    from pipeline.lead_score import score_lead
    from pipeline.qualification import extract_property_zip

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap", type=int, default=250)
    ap.add_argument("--out", default=str(Path.home() / "Downloads" / f"rent_targets_{date.today()}.csv"))
    a = ap.parse_args(argv)

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    today = date.today()

    def pg(t, s, b=lambda q: q):
        rows, off = [], 0
        while True:
            x = b(sb.table(t).select(s)).order("case_number").range(off, off + 999).execute().data or []
            rows += x
            if len(x) < 1000:
                break
            off += 1000
        return rows

    # empirical estimates (filings + ists)
    est = [(r.get("property_zip"), r.get("estimated_rent")) for r in
           pg("filings", "property_zip,estimated_rent", lambda q: q.not_.is_("estimated_rent", "null"))]
    est += [(extract_property_zip(r.get("property_address") or ""), r.get("estimated_rent")) for r in
            pg("ists_judgments", "property_address,estimated_rent", lambda q: q.not_.is_("estimated_rent", "null"))]
    yields = zip_yield(est)
    priority = {p["zip"] for p in sb.table("priority_zips").select("zip").execute().data or []}

    # candidates: good_leads_now missing rent
    cands = [r for r in pg("good_leads_now",
                           "case_number,tenant_name,property_address,property_zip,county,filing_date,estimated_rent")
             if not r.get("estimated_rent")]
    for r in cands:
        r["score"] = score_lead(rent=None, tenant_name=r.get("tenant_name") or "",
                                lead_date=date.fromisoformat(r["filing_date"]) if r.get("filing_date") else None,
                                today=today)
    ranked = select_targets(cands, yields, priority, cap=a.cap)

    from collections import Counter
    print(f"PROVEN ZIPs (n>={MIN_N}, %>=1600>={MIN_PCT:.0%}): {sum(1 for y in yields.values() if y['pct']>=MIN_PCT)}")
    print(f"Candidates missing rent: {len(cands)} | targetable (proven+priority): "
          f"{len(select_targets(cands, yields, priority))} | this batch: {len(ranked)}")
    print("Tier mix in batch:", dict(Counter(tier_of(r, yields, priority) for r in ranked)))
    print("Top ZIPs in batch:")
    for z, n in Counter(r["property_zip"] for r in ranked).most_common(12):
        y = yields.get(z)
        tag = f"proven {y['pct']:.0%}/${y['median']:,.0f} n={y['n']}" if y and y["pct"] >= MIN_PCT else "priority(unproven)"
        print(f"  {z}: {n}  [{tag}]")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["property_zip", "score", "case_number", "tenant_name",
                                          "property_address", "county", "filing_date"], extrasaction="ignore")
        w.writeheader()
        for r in ranked:
            w.writerow(r)
    print(f"\nWrote {len(ranked)} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
