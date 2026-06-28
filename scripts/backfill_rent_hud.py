"""Backfill estimated_rent from the free HUD SAFMR table (ZIP -> 2BR rent).

Unlike scripts/backfill_rent.py (Rentometer, paid + rate-limited), this uses the
national HUD Small Area Fair Market Rent file bundled at
resources/hud_safmr_fy2026.csv — free, no API key, no rate limit, ~38.6k ZIPs.
It only FILLS rows where estimated_rent IS NULL, so it never overwrites a precise
Rentometer value; it just gives every other lead a baseline rent so it can be
ranked (HUD is the 40th-pct subsidy rent — good for ordering, blunt at the luxury
high end; keep Rentometer as the precision layer on top leads). See
docs/hud_fmr_vs_rentometer_research.md.

Usage:
    python scripts/backfill_rent_hud.py --dry-run
    python scripts/backfill_rent_hud.py --track both --write
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client

from pipeline.qualification import extract_property_zip
from services.rent_estimate_service import _load_safmr_table, DEFAULT_SAFMR_PATH

log = logging.getLogger("backfill_rent_hud")

_BEDROOMS = 2  # apples-to-apples with Rentometer's 2BR default (see research memo)


def _safmr_table() -> dict[str, dict[int, float]]:
    path = os.getenv("HUD_SAFMR_DATA_PATH", "").strip() or str(DEFAULT_SAFMR_PATH)
    return _load_safmr_table(path)


def _zip_for(row: dict) -> str | None:
    z = (row.get("property_zip") or "").strip() if row.get("property_zip") else None
    return z or extract_property_zip(row.get("property_address") or "") or None


def _rent_for_zip(table: dict[str, dict[int, float]], zip_code: str) -> float | None:
    rents = table.get(zip_code.zfill(5))
    if not rents:
        return None
    return rents.get(_BEDROOMS) or rents.get(2)


def _fetch_null_rent(sb, table_name: str, cols: str) -> list[dict]:
    rows, off = [], 0
    while True:
        b = (sb.table(table_name).select(cols)
             .is_("estimated_rent", "null")
             .range(off, off + 999).execute().data or [])
        rows += b
        if len(b) < 1000:
            return rows
        off += 1000


def backfill(sb, table_name: str, cols: str, *, write: bool) -> dict:
    table = _safmr_table()
    rows = _fetch_null_rent(sb, table_name, cols)
    by_rent: dict[float, list[str]] = defaultdict(list)
    no_zip = no_match = 0
    for r in rows:
        z = _zip_for(r)
        if not z:
            no_zip += 1
            continue
        rent = _rent_for_zip(table, z)
        if rent is None:
            no_match += 1
            continue
        by_rent[rent].append(r["case_number"])
    matched = sum(len(v) for v in by_rent.values())
    if write:
        for rent, cases in by_rent.items():
            for i in range(0, len(cases), 200):
                (sb.table(table_name).update({"estimated_rent": rent})
                 .in_("case_number", cases[i:i + 200]).execute())
    return {"table": table_name, "null_rows": len(rows), "matched": matched,
            "no_zip": no_zip, "no_safmr_match": no_match}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--track", choices=["vantage", "ists", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.write:
        ap.error("pass --dry-run or --write")

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    write = a.write and not a.dry_run

    if a.track in {"vantage", "both"}:
        res = backfill(sb, "filings",
                       "case_number,property_zip,property_address", write=write)
        log.info("filings: %s%s", res, "" if write else "  [dry-run]")
    if a.track in {"ists", "both"}:
        res = backfill(sb, "ists_judgments",
                       "case_number,property_address", write=write)
        log.info("ists_judgments: %s%s", res, "" if write else "  [dry-run]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
