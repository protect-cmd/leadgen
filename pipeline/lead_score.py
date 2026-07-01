"""Lead score (0-100) for prioritizing enrichment and dial queues.

v2 rewards value first: estimated rent dominates the score, with name
match-likelihood and freshness as secondary ordering signals. Priority ZIP tier
still sorts outside the score in the queue builders.
"""
from __future__ import annotations

from datetime import date

from services.name_utils import clean_tenant_name, is_common_surname, parse_name

# Weight profiles (w_rent/w_match/w_fresh must sum to 100) + value floor/cap per
# business. NOTE: `rent`/`rent_floor`/`rent_cap` are really the VALUE-AMOUNT
# dimension — market rent for eviction businesses, debt amount for Cosner, and
# unused (w_rent=0) for Garnish Proof which has no amount.
_PROFILES = {
    "vantage": dict(w_rent=50, w_match=30, w_fresh=20, rent_floor=800.0, rent_cap=3500.0),
    # ISTS: judgments don't time-decay like pre-court filings and cluster near the
    # rent floor, so drop freshness (-> rent+match) and lower the floor for spread.
    "ists":    dict(w_rent=60, w_match=40, w_fresh=0,  rent_floor=1200.0, rent_cap=3500.0),
    # Cosner (debt x filed): value = debt amount; freshness matters (the ~30-day
    # Answer window before default judgment). Floor/cap span typical debt-claim sizes.
    "cosner":  dict(w_rent=50, w_match=30, w_fresh=20, rent_floor=1000.0, rent_cap=25000.0),
    # Garnish Proof (debt x judgment): no amount dimension, so weight name match +
    # writ freshness only (freshest writ = most urgent exemption window).
    "garnish_proof": dict(w_rent=0, w_match=50, w_fresh=50, rent_floor=0.0, rent_cap=1.0),
}
_COMMON_SURNAME_FACTOR = 0.55


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def score_lead(
    *,
    rent,
    tenant_name: str,
    lead_date: date | None,
    today: date,
    fresh_window_days: int = 21,
    profile: str = "vantage",
) -> int:
    """Return a 0-100 lead score using the named weight profile.

    rent is a market rent estimate; None contributes 0 rent points. lead_date is
    filing_date for Vantage and judgment_date for ISTS.
    """
    p = _PROFILES[profile]

    rent_pts = 0.0
    if rent:
        rent_pts = p["w_rent"] * _clamp(
            (float(rent) - p["rent_floor"]) / (p["rent_cap"] - p["rent_floor"])
        )

    first, last = parse_name(clean_tenant_name(tenant_name or ""))
    common = bool(last and is_common_surname(last))
    match_pts = p["w_match"] * (_COMMON_SURNAME_FACTOR if common else 1.0)

    if lead_date is None or not p["w_fresh"]:
        fresh_pts = 0.0
    else:
        age = (today - lead_date).days
        fresh_pts = p["w_fresh"] * _clamp((fresh_window_days - age) / fresh_window_days)

    return max(0, min(100, round(rent_pts + match_pts + fresh_pts)))


def compute_coverage_rates(sb, dnc_dir: str) -> dict[str, float]:
    """Legacy helper kept for callers until the coverage cleanup is complete."""
    import glob
    import os
    from collections import defaultdict

    covered = {
        os.path.basename(p).split("_")[1]
        for p in glob.glob(os.path.join(dnc_dir, "*.txt"))
    }

    def area(phone: str | None) -> str | None:
        d = "".join(c for c in (phone or "") if c.isdigit())
        if len(d) == 11 and d[0] == "1":
            d = d[1:]
        return d[:3] if len(d) == 10 else None

    lc, off = [], 0
    while True:
        b = (
            sb.table("lead_contacts")
            .select("case_number,phone")
            .eq("track", "ng")
            .not_.is_("phone", "null")
            .range(off, off + 999)
            .execute()
            .data
            or []
        )
        lc += b
        if len(b) < 1000:
            break
        off += 1000

    cases = [r["case_number"] for r in lc]
    county_of: dict[str, str] = {}
    for i in range(0, len(cases), 200):
        for f in (
            sb.table("filings")
            .select("case_number,county")
            .in_("case_number", cases[i : i + 200])
            .execute()
            .data
            or []
        ):
            county_of[f["case_number"]] = f["county"]

    tot: dict[str, int] = defaultdict(int)
    cov: dict[str, int] = defaultdict(int)
    for r in lc:
        c, a = county_of.get(r["case_number"]), area(r["phone"])
        if not c or not a:
            continue
        tot[c] += 1
        if a in covered:
            cov[c] += 1
    return {c: cov[c] / tot[c] for c in tot if tot[c]}
