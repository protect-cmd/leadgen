"""Lead score (0-100) for prioritizing enrichment and dial queues.

v2 rewards value first: estimated rent dominates the score, with name
match-likelihood and freshness as secondary ordering signals. Priority ZIP tier
still sorts outside the score in the queue builders.
"""
from __future__ import annotations

from datetime import date

from services.name_utils import clean_tenant_name, is_common_surname, parse_name

_W_RENT = 50
_W_MATCH = 30
_W_FRESH = 20
_RENT_FLOOR = 800.0
_RENT_CAP = 3500.0
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
) -> int:
    """Return a 0-100 lead score.

    rent is a market rent estimate; None contributes 0 rent points. lead_date is
    filing_date for Vantage and judgment_date for ISTS.
    """
    rent_pts = 0.0
    if rent:
        rent_pts = _W_RENT * _clamp((float(rent) - _RENT_FLOOR) / (_RENT_CAP - _RENT_FLOOR))

    first, last = parse_name(clean_tenant_name(tenant_name or ""))
    common = bool(last and is_common_surname(last))
    match_pts = _W_MATCH * (_COMMON_SURNAME_FACTOR if common else 1.0)

    if lead_date is None:
        fresh_pts = 0.0
    else:
        age = (today - lead_date).days
        fresh_pts = _W_FRESH * _clamp((fresh_window_days - age) / fresh_window_days)

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
