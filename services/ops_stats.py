"""Aggregations for the /ops dashboard.

Section functions (health_flags, scrapes, spend, funnel, trend) each take the
Supabase client (and the enrichment cache for spend) and return a plain dict.
get_ops_stats composes them with per-section fault isolation so one broken query
never blanks the page.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

_BLOCKS = "▁▂▃▄▅▆▇█"

# scheduled counties expected to scrape (from services/daily_scheduler.py)
_EXPECTED_COUNTIES = ("Harris", "Davidson", "Franklin", "Maricopa", "Hamilton")


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def with_pct(stages: list[dict]) -> list[dict]:
    """Annotate each stage with pct = count/prev_count*100 (rounded). First is None;
    a zero previous yields None (no divide-by-zero)."""
    out = []
    for i, s in enumerate(stages):
        if i == 0:
            pct = None
        else:
            prev = stages[i - 1]["count"]
            pct = round(s["count"] / prev * 100) if prev else None
        out.append({**s, "pct": pct})
    return out


def sparkline(values: list[float]) -> str:
    """Unicode sparkline. Empty -> ''. All-equal -> mid blocks."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return _BLOCKS[len(_BLOCKS) // 2] * len(values)
    span = hi - lo
    return "".join(_BLOCKS[int((v - lo) / span * (len(_BLOCKS) - 1))] for v in values)


# --------------------------------------------------------------------------- #
# fetch helpers
# --------------------------------------------------------------------------- #
def _paginate(sb, table, select, build=lambda q: q):
    rows, off = [], 0
    while True:
        b = build(sb.table(table).select(select)).order("case_number").range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def _paginate_rm(sb, gte: str) -> list:
    rows, off = [], 0
    while True:
        b = (sb.table("run_metrics").select("county,run_at,filings_received,duplicates_skipped,phones_found")
             .gte("run_at", gte).order("run_at").range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def funnel(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    w21 = (today - timedelta(days=21)).isoformat()
    w14 = (today - timedelta(days=14)).isoformat()

    # Vantage
    fil = _paginate(sb, "filings",
                    "case_number,is_enrichable,estimated_rent,filing_date,court_date",
                    lambda q: q.gte("filing_date", w21))
    enrich = [r for r in fil if r.get("is_enrichable")]
    ge1600 = [r for r in enrich if r.get("estimated_rent") and float(r["estimated_rent"]) >= 1600]
    ge_cns = {r["case_number"] for r in ge1600}
    lc = _paginate(sb, "lead_contacts",
                   "case_number,phone,dnc_status,bland_call_id,ghl_contact_id",
                   lambda q: q.eq("track", "ng"))
    lc_ge = [r for r in lc if r["case_number"] in ge_cns]
    phoned = [r for r in lc_ge if r.get("phone")]
    callable_ = [r for r in phoned if r.get("dnc_status") == "callable"]
    fired = [r for r in lc_ge if r.get("bland_call_id")]
    staged = [r for r in lc_ge if r.get("ghl_contact_id")]
    vantage = {
        "stages": with_pct([
            {"label": "Scraped", "count": len(fil)},
            {"label": "Enrichable", "count": len(enrich)},
            {"label": "Rent >= $1600", "count": len(ge1600)},
            {"label": "Phone found", "count": len(phoned)},
            {"label": "Callable", "count": len(callable_)},
        ]),
        "outcomes": {"fired": len(fired), "staged": len(staged)},
    }

    # ISTS
    j = _paginate(sb, "ists_judgments",
                  "case_number,judgment_date,estimated_rent,phone,dnc_status,bland_call_id,ghl_contact_id")
    fresh = [r for r in j if (r.get("judgment_date") or "") >= w14]
    jge = [r for r in fresh if r.get("estimated_rent") and float(r["estimated_rent"]) >= 1600]
    jphone = [r for r in jge if r.get("phone")]
    jcall = [r for r in jphone if r.get("dnc_status") == "callable"]
    jfired = [r for r in jge if r.get("bland_call_id")]
    jstaged = [r for r in jge if r.get("ghl_contact_id")]
    ists = {
        "stages": with_pct([
            {"label": "Scraped", "count": len(j)},
            {"label": "Fresh (14d)", "count": len(fresh)},
            {"label": "Rent >= $1600", "count": len(jge)},
            {"label": "Phone found", "count": len(jphone)},
            {"label": "Callable", "count": len(jcall)},
        ]),
        "outcomes": {"fired": len(jfired), "staged": len(jstaged)},
    }
    return {"vantage": vantage, "ists": ists}


def scrapes(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    lo = today.isoformat()
    rm = _paginate_rm(sb, gte=(today - timedelta(days=7)).isoformat())
    by_county_today: dict[str, dict] = {}
    spark: dict[str, list] = {}
    for r in rm:
        c = (r.get("county") or "").replace(" County", "")
        spark.setdefault(c, []).append(r)
        if (r.get("run_at") or "") >= lo:
            by_county_today[c] = r
    rows = []
    for c in _EXPECTED_COUNTIES:
        t = by_county_today.get(c)
        hist = sorted(spark.get(c, []), key=lambda x: x.get("run_at") or "")
        rows.append({
            "county": c,
            "received": (t or {}).get("filings_received"),
            "new": ((t or {}).get("filings_received", 0) - (t or {}).get("duplicates_skipped", 0)) if t else None,
            "dupes": (t or {}).get("duplicates_skipped"),
            "last_run": ((t or {}).get("run_at") or "")[11:16] if t else None,
            "missing": t is None,
            "spark7": sparkline([h.get("filings_received", 0) for h in hist]),
        })
    return {"rows": rows}


def spend(cache) -> dict:
    cred, cred_ts = cache.get_ops_value_with_ts("rentometer_credits")
    return {
        "searchbug_today": cache.daily_count("searchbug"),
        "searchbug_cap": int(os.getenv("SEARCHBUG_DAILY_CAP", "100")),
        "bland_today": cache.daily_count("bland"),
        "bland_cap": int(os.getenv("BLAND_DAILY_CAP", "100")),
        "rentometer_credits": int(cred) if cred is not None else None,
        "rentometer_as_of": cred_ts,
    }


def health_flags(sb, cache, *, today: date | None = None) -> dict:
    today = today or date.today()
    flags: list[dict] = []
    fil = _paginate(sb, "filings", "county,scraped_at,enrichable_checked_at")
    last_scrape: dict[str, str] = {}
    last_checked = ""
    for r in fil:
        c = (r.get("county") or "").replace(" County", "")
        sa = r.get("scraped_at") or ""
        if sa > last_scrape.get(c, ""):
            last_scrape[c] = sa
        ca = r.get("enrichable_checked_at") or ""
        if ca > last_checked:
            last_checked = ca
    cutoff = (today - timedelta(days=7)).isoformat()
    for c in _EXPECTED_COUNTIES:
        ls = last_scrape.get(c, "")
        if ls and ls[:10] < cutoff:
            days = (today - date.fromisoformat(ls[:10])).days
            flags.append({"level": "red", "msg": f"{c}: dark — no filings in {days}d"})
    if last_checked[:10] != today.isoformat():
        flags.append({"level": "warn", "msg": "post-scrape chain hasn't run today"})
    if not cache.check_daily_cap(int(os.getenv("BLAND_DAILY_CAP", "100")), kind="bland"):
        flags.append({"level": "warn", "msg": "Bland at daily cap"})
    if not os.getenv("DNCSCRUB_LOGIN_ID", "").strip():
        flags.append({"level": "warn", "msg": "DNCScrub not configured (local-files only)"})
    if not flags:
        flags.append({"level": "ok", "msg": "All systems nominal"})
    return {"flags": flags}


def trend(sb, *, today: date | None = None) -> dict:
    today = today or date.today()
    days = [(today - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    rm = _paginate_rm(sb, gte=days[0])
    filings = {d: 0 for d in days}
    phones = {d: 0 for d in days}
    for r in rm:
        d = (r.get("run_at") or "")[:10]
        if d in filings:
            filings[d] += r.get("filings_received") or 0
            phones[d] += r.get("phones_found") or 0
    fired = {d: 0 for d in days}
    try:
        for table in ("lead_contacts", "ists_judgments"):
            for r in _paginate(sb, table, "case_number,bland_triggered_at",
                               lambda q: q.gte("bland_triggered_at", days[0])):
                fd = (r.get("bland_triggered_at") or "")[:10]
                if fd in fired:
                    fired[fd] += 1
    except Exception:
        pass  # bland_triggered_at absent until migration 021 lands — keep filings/phones
    return {"filings": [filings[d] for d in days], "phones": [phones[d] for d in days],
            "fired": [fired[d] for d in days], "days": days}


def get_ops_stats(sb, cache, *, today: date | None = None) -> dict:
    today = today or date.today()
    out = {"as_of": datetime.now(timezone.utc).isoformat()}
    sections = {
        "health": lambda: health_flags(sb, cache, today=today),
        "scrapes": lambda: scrapes(sb, today=today),
        "spend": lambda: spend(cache),
        "funnel": lambda: funnel(sb, today=today),
        "trend": lambda: trend(sb, today=today),
    }
    for name, fn in sections.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": repr(e)[:160]}
    return out
