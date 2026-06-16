# scrapers/ohio/franklin_judgments.py
"""ISTS sub-project A2 — Franklin County OH "tenant-lost" judgment sourcing.

The Franklin County Municipal Court Clerk publishes a monthly, no-auth CSV
("FCMC Civil F.E.D. (Eviction) Case List") that carries an explicit disposition
outcome AND a full defendant address. This is the closest analog to the Harris
JP "Judgments Entered" extract (scrapers/texas/harris_judgments.py).

This module reuses the column constants + parsing helpers from the existing
filings scraper (scrapers/ohio/franklin.py) read-only; it does NOT modify it.
The pure parser (parse_eviction_judgments_csv) is browser-free and unit-tested.

Tenant-lost is a fixed set of LAST_DISPOSITION_DESCRIPTION values, verified
2026-06-16 against FCMC case-detail pages (JUDGMENT HEARD BY MAGISTRATE =
"JUDGEMENT FOR RESTITUTION OF PREMISES" + writ of restitution issued). See
docs/superpowers/specs/2026-06-16-ists-franklin-judgment-leads-design.md.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

import requests

from models.judgment import JudgmentRecord
from pipeline.gates import gate_address, gate_name
from scrapers.dates import court_today

# Reuse the filings scraper's column constants + helpers (read-only).
from scrapers.ohio.franklin import (
    BASE_URL,
    REPORTS_URL,
    F_CASE_NUMBER,
    F_FILE_DATE,
    F_PLAINTIFF_COMPANY,
    F_PLAINTIFF_FIRST,
    F_PLAINTIFF_MIDDLE,
    F_PLAINTIFF_LAST,
    F_PLAINTIFF_SUFFIX,
    F_DEF_COMPANY,
    F_DEF_FIRST,
    F_DEF_MIDDLE,
    F_DEF_LAST,
    F_DEF_SUFFIX,
    F_DEF_ADDR1,
    F_DEF_ADDR2,
    F_DEF_CITY,
    F_DEF_STATE,
    F_DEF_ZIP,
    _build_address,
    _discover_report_links,
    _party_name,
)

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Franklin"
COURT_TIMEZONE = "America/New_York"
SOURCE_URL = REPORTS_URL

# The filings scraper does not define a constant for the disposition columns
# (it ignores them), so name them here.
F_DISPOSITION = "LAST_DISPOSITION_DESCRIPTION"
F_DISPO_DATE = "LAST_DISPOSITION_DATE"

# Verified tenant-lost dispositions (uppercased for case-insensitive match).
# OTHER TERMINATION - ADMIN JUDGE is deliberately EXCLUDED in v1 (mixed bucket).
TENANT_LOST_DISPOSITIONS = frozenset({
    "JUDGMENT HEARD BY MAGISTRATE",
    "JUDGMENT FOR PLAINTIFF",
    "AGREED JUDGMENT BOTH CAUSE OF ACTION",
})

# Disposition-date lookback window. Disposition lags filing, and the CSV is not
# server-side date-filtered, so we window client-side on LAST_DISPOSITION_DATE.
FLOOR_DAYS = 3       # skip dispositions newer than this (posting/data lag buffer)
CEILING_DAYS = 30    # within the W1 legal window; tune from the run metrics


def _safe_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_eviction_judgments_csv(csv_text: str) -> list[JudgmentRecord]:
    """Parse an FCMC eviction CSV and return tenant-lost JudgmentRecords.

    Keeps only rows whose LAST_DISPOSITION_DESCRIPTION is a tenant-lost
    disposition, with an individual (non-entity) defendant and a full address.
    """
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))
    out: list[JudgmentRecord] = []
    for row in reader:
        case_number = (row.get(F_CASE_NUMBER, "") or "").strip()
        try:
            disposition = (row.get(F_DISPOSITION, "") or "").strip()
            if disposition.upper() not in TENANT_LOST_DISPOSITIONS:
                continue

            tenant = _party_name(
                row.get(F_DEF_COMPANY, ""), row.get(F_DEF_FIRST, ""),
                row.get(F_DEF_MIDDLE, ""), row.get(F_DEF_LAST, ""),
                row.get(F_DEF_SUFFIX, ""),
            )
            if not gate_name(tenant):
                continue

            address = _build_address(
                row.get(F_DEF_ADDR1, ""), row.get(F_DEF_ADDR2, ""),
                row.get(F_DEF_CITY, ""), row.get(F_DEF_STATE, ""),
                row.get(F_DEF_ZIP, ""),
            )
            if not gate_address(address):
                continue

            plaintiff = _party_name(
                row.get(F_PLAINTIFF_COMPANY, ""), row.get(F_PLAINTIFF_FIRST, ""),
                row.get(F_PLAINTIFF_MIDDLE, ""), row.get(F_PLAINTIFF_LAST, ""),
                row.get(F_PLAINTIFF_SUFFIX, ""),
            )
            dispo_date = _safe_date(row.get(F_DISPO_DATE, ""))

            out.append(JudgmentRecord(
                case_number=case_number,
                defendant_name=tenant,  # preserve raw fidelity for SearchBug matching
                property_address=address,
                plaintiff_name=plaintiff or None,
                state=STATE,
                county=COUNTY,
                judgment_date=dispo_date,
                disposition_desc=disposition or None,
                disposition_date=dispo_date,
                window="W1",
                source_url=SOURCE_URL,
            ))
        except Exception as e:
            log.warning("Franklin ISTS: skipped row %s: %s", case_number or "?", e)
            continue
    return out


def filter_by_disposition_window(
    records: list[JudgmentRecord], *, today: date, floor_days: int, ceiling_days: int,
) -> list[JudgmentRecord]:
    """Keep records whose disposition (judgment) date is in [today-ceiling, today-floor]."""
    out: list[JudgmentRecord] = []
    for r in records:
        d = r.judgment_date
        if d is None:
            continue
        age = (today - d).days
        if floor_days <= age <= ceiling_days:
            out.append(r)
    return out


class FranklinJudgmentScraper:
    """Downloads the FCMC eviction CSV(s) covering the disposition window and
    returns tenant-lost JudgmentRecords. Plain requests — no browser."""

    def __init__(self, floor_days: int = FLOOR_DAYS, ceiling_days: int = CEILING_DAYS):
        self.floor_days = floor_days
        self.ceiling_days = ceiling_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[JudgmentRecord]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)

        try:
            index_html = self._get_text(REPORTS_URL)
        except Exception as e:
            self.last_error = f"failed to fetch FCMC eviction report index: {e}"
            log.error("Franklin ISTS: failed to fetch report index: %s", e)
            return []

        # Window on disposition date back to CEILING days; pull every monthly file
        # that overlaps that range so dispositions near a month boundary aren't missed.
        links = _discover_report_links(
            index_html, today=today, lookback_days=self.ceiling_days,
        )
        if not links:
            self.last_error = "no FCMC eviction report links found"
            return []

        records: list[JudgmentRecord] = []
        seen: set[str] = set()
        for link in links:
            try:
                csv_text = self._get_text(link.url)
            except Exception as e:
                log.warning("Franklin ISTS: failed to fetch %s: %s", link.url, e)
                continue
            for r in parse_eviction_judgments_csv(csv_text):
                if r.case_number in seen:
                    continue
                seen.add(r.case_number)
                r.source_url = link.url
                records.append(r)

        windowed = filter_by_disposition_window(
            records, today=today, floor_days=self.floor_days, ceiling_days=self.ceiling_days,
        )
        log.info("Franklin ISTS: %d tenant-lost judgments in window (of %d parsed)",
                 len(windowed), len(records))
        return windowed

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
