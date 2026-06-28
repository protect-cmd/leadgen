"""Garnish Proof importer (PLAN.md Phase 7).

Loads the manually-extracted Florida garnishment-writ spreadsheet into
garnishment_orders so GP can produce leads NOW, before an automated scraper
exists. The spreadsheet is the source behind the shared data contract — when an
automated scraper is built it drops in behind the same gp_store/contract with no
pipeline changes.

Mapping (writs sheet):
    Case Number                     -> case_number
    Defendant  Name ("LAST, FIRST") -> debtor_name (split downstream by name_utils)
    Defendant Street Address        -> debtor_address (whitespace-normalized)
    Plaintiff (Creditor)            -> creditor_name
    Garnishee                       -> garnishee_name
    Writ of Garnishment Filed Date  -> filing_date  (THE freshness anchor)
    Writ of Garnishment Issued Date -> + GP_EXEMPTION_WINDOW_DAYS -> exemption_deadline
    county defaults to Hillsborough (the table default is Miami-Dade)

Idempotency: gp_store upserts on case_number, and multiple garnishee rows share a
case_number, so re-imports update (not duplicate) and collapse to one lead/debtor.

Usage:
    python scripts/import_gp_garnishment_xlsx.py --path "<file.xlsx>"            # dry run
    python scripts/import_gp_garnishment_xlsx.py --path "<file.xlsx>" --yes-write-supabase
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.garnishment import GarnishmentRecord  # noqa: E402

log = logging.getLogger("gp_import")

DEFAULT_SHEET = "Garnishment Writs (Filed & Issu"
DEFAULT_COUNTY = "Hillsborough"
_EXEMPTION_WINDOW_DAYS = int(os.getenv("GP_EXEMPTION_WINDOW_DAYS", "20"))

# Column headers in the spreadsheet (note the double space in "Defendant  Name").
C_CASE = "Case Number"
C_NAME = "Defendant  Name"
C_ADDR = "Defendant Street Address"
C_CREDITOR = "Plaintiff (Creditor)"
C_GARNISHEE = "Garnishee"
C_WRIT_FILED = "Writ of Garnishment Filed Date"
C_WRIT_ISSUED = "Writ of Garnishment Issued Date"

_WS_RE = re.compile(r"\s+")


def _clean(s) -> str:
    """Collapse tabs/newlines/repeated spaces (the sheet has 'TAMPA\\tFL') to single
    spaces and strip. Returns '' for NaN/None."""
    if s is None:
        return ""
    text = str(s)
    if text.strip().lower() in ("nan", "nat", "none"):
        return ""
    return _WS_RE.sub(" ", text).strip()


def _to_date(v) -> date | None:
    """Coerce a cell (pandas Timestamp / datetime / date / 'YYYY-MM-DD' str) to a date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat"):
        return None
    try:
        return datetime.fromisoformat(s.split(" ")[0].split("T")[0]).date()
    except ValueError:
        return None


def _to_record(row: dict, *, county: str = DEFAULT_COUNTY) -> GarnishmentRecord | None:
    """Map one spreadsheet row (dict keyed by header) to a GarnishmentRecord, or
    None if the row lacks the essentials (case number + name + street address)."""
    case_number = _clean(row.get(C_CASE))
    debtor_name = _clean(row.get(C_NAME))
    debtor_address = _clean(row.get(C_ADDR))
    if not (case_number and debtor_name and debtor_address):
        return None

    issued = _to_date(row.get(C_WRIT_ISSUED))
    exemption_deadline = (
        issued + timedelta(days=_EXEMPTION_WINDOW_DAYS) if issued else None
    )
    return GarnishmentRecord(
        case_number=case_number,
        debtor_name=debtor_name,
        debtor_address=debtor_address,
        creditor_name=_clean(row.get(C_CREDITOR)) or None,
        garnishee_name=_clean(row.get(C_GARNISHEE)) or None,
        state="FL",
        county=county,
        filing_date=_to_date(row.get(C_WRIT_FILED)),
        garnishment_type="wage",
        exemption_deadline=exemption_deadline,
        source_url="manual_import:florida_wage_garnishment_xlsx",
    )


def rows_to_records(rows: list[dict], *, county: str = DEFAULT_COUNTY) -> list[GarnishmentRecord]:
    """Map + dedupe rows to one record per case_number (first occurrence wins —
    multiple garnishee rows for the same debtor collapse to a single lead)."""
    out: dict[str, GarnishmentRecord] = {}
    for row in rows:
        rec = _to_record(row, county=county)
        if rec and rec.case_number not in out:
            out[rec.case_number] = rec
    return list(out.values())


def read_xlsx(path: str, sheet: str = DEFAULT_SHEET) -> list[dict]:
    """Read the spreadsheet sheet into a list of header-keyed row dicts."""
    import pandas as pd

    df = pd.read_excel(path, sheet_name=sheet)
    return df.to_dict(orient="records")


async def _write(records: list[GarnishmentRecord]) -> int:
    from services import gp_store

    written = 0
    for rec in records:
        await gp_store.upsert_order(rec)
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", required=True, help="path to the .xlsx file")
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--county", default=DEFAULT_COUNTY)
    ap.add_argument("--limit", type=int, default=0, help="cap records (0 = all)")
    ap.add_argument("--yes-write-supabase", action="store_true",
                    help="actually upsert to garnishment_orders (default: dry run)")
    args = ap.parse_args(argv)

    rows = read_xlsx(args.path, args.sheet)
    records = rows_to_records(rows, county=args.county)
    if args.limit:
        records = records[: args.limit]

    with_addr = sum(1 for r in records if r.debtor_address)
    with_date = sum(1 for r in records if r.filing_date)
    log.info("parsed %d rows -> %d unique leads (%d with address, %d with writ date)",
             len(rows), len(records), with_addr, with_date)
    for r in records[:5]:
        log.info("  %s | %s | %s | writ %s | exempt %s",
                 r.case_number, r.debtor_name, r.debtor_address, r.filing_date,
                 r.exemption_deadline)

    if not args.yes_write_supabase:
        log.info("DRY RUN — pass --yes-write-supabase to upsert into garnishment_orders")
        return 0

    import asyncio
    written = asyncio.run(_write(records))
    log.info("upserted %d garnishment_orders rows", written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
