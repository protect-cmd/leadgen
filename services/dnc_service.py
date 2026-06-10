"""Unified DNC verdict — DNCScrub.com API when configured, local FTC files otherwise.

Replaces the per-script local-file `on_dnc()` with a single national scrub so the
"held" outcome (out-of-scope area code) disappears: every phone gets callable/dnc.

INERT until DNCSCRUB_LOGIN_ID is set — with no key it behaves exactly as today
(local files + DNC_FAIL_CLOSED). See
docs/superpowers/specs/2026-06-10-dncscrub-integration-research.md.
"""
from __future__ import annotations

import glob
import os

import httpx

_API = "https://www.dncscrub.com/app/main/rpc/scrub"
_DNC_DIR = os.getenv("DNC_DIR", r"C:\Users\Zeann\Downloads\DNC Scrub")
_dnc_cache: dict[str, set | None] = {}

# DNC = actually on a registry (Federal/State/Internal). The authoritative signal
# is the Reason field; ResultCode is a hint. L/F ("wireless prohibited in state")
# are NOT DNC-list members — they're clean cells flagged only for autodialer-wireless
# state rules, so we treat them as callable (Bland dials a specific list, not an ATDS).
_CALLABLE_CODES = {"C", "W", "G", "H", "B", "L", "F"}
_DNC_CODES = {"D"}        # registry do-not-call (always carries a Reason segment too)
_INVALID_CODES = {"I"}    # invalid / directory-assistance — don't dial


def result_code_verdict(code: str | None) -> str:
    """Map a DNCScrub ResultCode to callable | dnc | unknown (pure, testable)."""
    c = (code or "").strip().upper()[:1]
    if c in _DNC_CODES:
        return "dnc"
    if c in _CALLABLE_CODES:
        return "callable"
    return "unknown"


def row_verdict(row: dict) -> str:
    """Authoritative per-row verdict: the Reason field is delimited
    'National;State;Internal;Wireless' — any populated DNC segment (first three) =
    dnc, regardless of ResultCode. Falls back to the code otherwise."""
    code = (row.get("ResultCode") or "").strip().upper()[:1]
    if code in _DNC_CODES:
        return "dnc"
    segs = (row.get("Reason") or "").split(";")
    if any(s.strip() for s in segs[:3]):   # on National / State / Internal DNC
        return "dnc"
    if code in _INVALID_CODES:
        return "unknown"
    return "callable"


def _digits(phone: str | None) -> str | None:
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d if len(d) == 10 else None


def _local_set(area: str):
    if area in _dnc_cache:
        return _dnc_cache[area]
    m = glob.glob(os.path.join(_DNC_DIR, f"*_{area}_*.txt"))
    if not m:
        _dnc_cache[area] = None
        return None
    s = set()
    with open(m[0], encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(",")
            if len(p) == 2:
                s.add(p[1])
    _dnc_cache[area] = s
    return s


def _local_verdict(phone: str | None) -> str:
    """callable | dnc | unknown from the local FTC files (unknown = no file = old 'held')."""
    d = _digits(phone)
    if not d:
        return "unknown"
    s = _local_set(d[:3])
    if s is None:
        return "unknown"
    return "dnc" if d[3:] in s else "callable"


def _fail_closed() -> bool:
    return (os.getenv("DNC_FAIL_CLOSED", "true") or "").strip().lower() in {"1", "true", "yes", "on"}


def _api_verdicts(phones: list[str]) -> dict[str, str]:
    """Scrub via DNCScrub. Returns {10-digit: verdict}. Empty dict on any failure."""
    login = os.getenv("DNCSCRUB_LOGIN_ID", "")
    if not login:
        return {}
    norm = [d for d in (_digits(p) for p in phones) if d]
    if not norm:
        return {}
    params = {"loginId": login, "phoneList": ",".join(norm), "version": "5", "output": "json"}
    proj = os.getenv("DNCSCRUB_PROJ_ID")
    if proj:
        params["projId"] = proj
    try:
        r = httpx.get(_API, params=params, timeout=30)
        if r.status_code != 200:
            return {}
        out: dict[str, str] = {}
        for row in r.json():
            d = _digits(str(row.get("Phone")))
            if d:
                out[d] = row_verdict(row)
        return out
    except Exception:
        return {}


def verdict(phone: str) -> str:
    """callable | dnc for one phone. API first, then local files, then fail-closed."""
    d = _digits(phone)
    if not d:
        return "dnc" if _fail_closed() else "callable"
    api = _api_verdicts([d]).get(d)
    v = api or _local_verdict(d)
    if v == "unknown":
        return "dnc" if _fail_closed() else "callable"
    return v


def verdict_many(phones: list[str], chunk: int = 100) -> dict[str, str]:
    """Batch verdicts {10-digit: callable|dnc}. Uses the API in chunks, local fallback per-miss."""
    norm = [d for d in (_digits(p) for p in phones) if d]
    result: dict[str, str] = {}
    for i in range(0, len(norm), chunk):
        result.update(_api_verdicts(norm[i:i + chunk]))
    for d in norm:
        if d not in result or result[d] == "unknown":
            lv = _local_verdict(d)
            result[d] = lv if lv != "unknown" else ("dnc" if _fail_closed() else "callable")
    return result
