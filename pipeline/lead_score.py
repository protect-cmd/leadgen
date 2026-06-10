"""Lead score (0-100) — ranks how well a SearchBug query is likely to spend.

This is the "optimize for the query" layer on top of is_enrichable. A lead is
already enrichable (clean name + street+ZIP); the score decides ORDER so we fire
SearchBug at the best-match, least-wasteful leads first. It rewards:

    match-likelihood (40) — clean, UNCOMMON surname (common surnames match weaker
                            even with a ZIP); all enrichable leads already have street+ZIP
    coverage         (35) — property metro historically yields in-DNC-scope (callable)
                            numbers, so fewer paid-for phones get held out-of-scope
    freshness        (25) — a just-served tenant is most receptive

The morning queue orders by (priority_rank NULLS LAST, score DESC), so priority
ZIPs still come first; the score ranks within and across the rent tail.

Coverage rates are learned from already-enriched phones (compute_coverage_rates).
"""
from __future__ import annotations

from datetime import date

from services.name_utils import clean_tenant_name, is_common_surname, parse_name

_W_MATCH = 40
_W_COVERAGE = 35
_W_FRESH = 25
_COMMON_SURNAME_FACTOR = 0.55   # weaker match confidence for common surnames
_UNKNOWN_COVERAGE = 0.5         # neutral prior for a county with no history


def score_lead(
    *,
    tenant_name: str,
    filing_date: date | None,
    county: str,
    coverage_rates: dict[str, float],
    today: date,
    fresh_window_days: int = 21,
) -> int:
    """Return a 0-100 lead score. coverage_rates maps county -> 0..1 in-scope rate."""
    first, last = parse_name(clean_tenant_name(tenant_name or ""))
    common = bool(last and is_common_surname(last))
    match = _W_MATCH * (_COMMON_SURNAME_FACTOR if common else 1.0)

    rate = coverage_rates.get(county, _UNKNOWN_COVERAGE)
    coverage = _W_COVERAGE * max(0.0, min(1.0, rate))

    if filing_date is None:
        fresh = 0.0
    else:
        age = (today - filing_date).days
        fresh = _W_FRESH * max(0.0, min(1.0, (fresh_window_days - age) / fresh_window_days))

    return max(0, min(100, round(match + coverage + fresh)))


def compute_coverage_rates(sb, dnc_dir: str) -> dict[str, float]:
    """In-DNC-scope rate per county from already-enriched ng phones.

    rate = (enriched phones whose area code has a local DNC file) / (enriched phones),
    grouped by the filing's county. Used as the coverage signal in score_lead.
    """
    import glob
    import os
    from collections import defaultdict

    covered = {os.path.basename(p).split("_")[1]
               for p in glob.glob(os.path.join(dnc_dir, "*.txt"))}

    def area(phone: str | None) -> str | None:
        d = "".join(c for c in (phone or "") if c.isdigit())
        if len(d) == 11 and d[0] == "1":
            d = d[1:]
        return d[:3] if len(d) == 10 else None

    lc, off = [], 0
    while True:
        b = (sb.table("lead_contacts").select("case_number,phone").eq("track", "ng")
             .not_.is_("phone", "null").range(off, off + 999).execute().data or [])
        lc += b
        if len(b) < 1000:
            break
        off += 1000

    cases = [r["case_number"] for r in lc]
    county_of: dict[str, str] = {}
    for i in range(0, len(cases), 200):
        for f in (sb.table("filings").select("case_number,county")
                  .in_("case_number", cases[i:i + 200]).execute().data or []):
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
