"""Backfill estimated_rent from Rentometer for scored queue leads.

Usage:
    python scripts/backfill_rent.py --track vantage --cap 300
    python scripts/backfill_rent.py --track ists --cap 100
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client

from pipeline.qualification import extract_property_zip
from scripts.rent_targeting import zip_yield, select_targets

_CTX = ssl._create_unverified_context()


def _zip_yields(sb) -> dict:
    """Empirical per-ZIP yield (median, %>=1600, n) from prior estimates."""
    est: list = []
    off = 0
    while True:
        b = (sb.table("filings").select("property_zip,estimated_rent")
             .not_.is_("estimated_rent", "null").not_.is_("property_zip", "null")
             .range(off, off + 999).execute().data or [])
        est += [(r.get("property_zip"), r.get("estimated_rent")) for r in b]
        if len(b) < 1000:
            break
        off += 1000
    off = 0
    while True:
        b = (sb.table("ists_judgments").select("property_address,estimated_rent")
             .not_.is_("estimated_rent", "null")
             .range(off, off + 999).execute().data or [])
        est += [(extract_property_zip(r.get("property_address") or ""), r.get("estimated_rent")) for r in b]
        if len(b) < 1000:
            break
        off += 1000
    return zip_yield(est)


def _select_vantage(rows: list[dict], yields: dict, priority: set, cap: int,
                    all_zips: bool = False) -> list[dict]:
    """Pick the Vantage Rentometer batch. Default: yield-based (proven ZIPs first,
    tail ZIPs dropped). --all-zips falls back to the old priority/recency order
    for backfilling older inventory regardless of proven yield."""
    if all_zips:
        return _order_scored_backfill_rows(rows, cap)
    return select_targets(rows, yields, priority, cap=cap)


def rentometer_median(address: str, bedrooms: int = 2):
    api_key = os.environ.get("RENTOMETER_API_KEY")
    if not api_key:
        return None
    q = urllib.parse.urlencode({"api_key": api_key, "address": address, "bedrooms": bedrooms})
    try:
        resp = urllib.request.urlopen(
            f"https://www.rentometer.com/api/v1/summary?{q}",
            timeout=30,
            context=_CTX,
        )
        d = json.loads(resp.read())
        credits = d.get("credits_remaining")
        if credits is not None:
            try:
                from services.enrichment_cache import get_cache
                get_cache().set_ops_value("rentometer_credits", str(int(credits)))
            except Exception:
                pass  # never let bookkeeping break a rent lookup
        return None if d.get("error") else d.get("median")
    except HTTPError as exc:
        if exc.code in {401, 402, 403}:
            raise RuntimeError(f"Rentometer API returned HTTP {exc.code}: {exc.reason}") from exc
        return None
    except Exception:
        return None


def _order_scored_backfill_rows(rows: list[dict], cap: int) -> list[dict]:
    rows.sort(
        key=lambda r: (
            r.get("priority_rank") is None,
            r.get("priority_rank") or 0,
            [-ord(c) for c in (r.get("filing_date") or "")],
        )
    )
    return rows[:cap]


def _priority_map(sb) -> dict[str, tuple[int, str]]:
    return {
        p["zip"]: (p["queue_rank"], p["metro"])
        for p in (sb.table("priority_zips").select("zip,queue_rank,metro").execute().data or [])
    }


def _prepare_ists_backfill_rows(
    rows: list[dict],
    priority: dict[str, tuple[int, str]],
    cap: int,
) -> list[dict]:
    for r in rows:
        z = extract_property_zip(r.get("property_address") or "")
        rank, metro = priority.get(z, (None, None))
        r["priority_rank"] = rank
        r["priority_metro"] = metro
        r["filing_date"] = r.get("judgment_date")
    return _order_scored_backfill_rows(rows, cap)


def _apply_extracted_date_filter(query, column: str, day: str | None):
    if not day:
        return query
    start = date.fromisoformat(day)
    end = start + timedelta(days=1)
    return query.gte(column, f"{start.isoformat()}T00:00:00").lt(
        column,
        f"{end.isoformat()}T00:00:00",
    )


def _fetch_vantage_rows(sb, extracted_date: str | None = None) -> list[dict]:
    rows, off = [], 0
    while True:
        q = (
            sb.table("good_leads_now")
            .select("case_number,property_address,property_zip,priority_rank,filing_date")
            .is_("estimated_rent", "null")
            .not_.is_("property_address", "null")
        )
        q = _apply_extracted_date_filter(q, "scraped_at", extracted_date)
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def _fetch_ists_rows(sb, extracted_date: str | None = None) -> list[dict]:
    rows, off = [], 0
    while True:
        q = (
            sb.table("ists_judgments")
            .select("case_number,property_address,judgment_date,state,county")
            .is_("estimated_rent", "null")
            .not_.is_("property_address", "null")
            .order("judgment_date", desc=True)
        )
        q = _apply_extracted_date_filter(q, "selected_at", extracted_date)
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def _store_rent(sb, table: str, case_number: str, rent: float) -> None:
    sb.table(table).update({"estimated_rent": rent}).eq("case_number", case_number).execute()


def _run_rows(sb, rows: list[dict], *, table: str, label: str) -> tuple[int, int]:
    done = found = 0
    print(f"rent backfill ({label}): {len(rows)}", flush=True)
    for r in rows:
        med = rentometer_median(r["property_address"])
        if med:
            _store_rent(sb, table, r["case_number"], med)
            found += 1
        done += 1
        if done % 100 == 0:
            print(f"  {done}/{len(rows)} | rent found {found}", flush=True)
        time.sleep(0.1)
    print(f"{label} done: {done} processed | {found} rents stored", flush=True)
    return done, found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--track", choices=["vantage", "ists", "both"], default="vantage")
    ap.add_argument("--cap", type=int, default=300, help="max Rentometer calls this run")
    ap.add_argument("--extracted-date", help="YYYY-MM-DD; Vantage scraped_at / ISTS selected_at")
    ap.add_argument("--all-zips", action="store_true",
                    help="Vantage: skip the proven-ZIP yield filter (backfill older inventory)")
    ap.add_argument("--limit", type=int, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.limit is not None:
        a.cap = a.limit

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    if a.track in {"vantage", "both"}:
        yields = {} if a.all_zips else _zip_yields(sb)
        priority = {p["zip"] for p in (sb.table("priority_zips").select("zip").execute().data or [])}
        rows = _select_vantage(_fetch_vantage_rows(sb, a.extracted_date), yields, priority,
                               a.cap, all_zips=a.all_zips)
        _run_rows(sb, rows, table="filings", label=f"vantage yield-targeted leads, cap {a.cap}")

    if a.track in {"ists", "both"}:
        rows = _prepare_ists_backfill_rows(_fetch_ists_rows(sb, a.extracted_date), _priority_map(sb), a.cap)
        _run_rows(sb, rows, table="ists_judgments", label=f"ists judgments, cap {a.cap}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
