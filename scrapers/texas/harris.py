from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import Download

from models.filing import Filing
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

log = logging.getLogger(__name__)

PORTAL_URL = "https://jpwebsite.harriscountytx.gov/PublicExtracts/search.jsp"
SOURCE_URL = PORTAL_URL

STATE = "TX"
COUNTY = "Harris"
COURT_TIMEZONE = "America/Chicago"

# ---------------------------------------------------------------------------
# Confirmed form selectors (verified 2026-05-01 via scratch_harris_discover.py)
# ---------------------------------------------------------------------------
SELECTOR_RADIO_CIVIL = "input#civil"               # value="CV"
SELECTOR_EXTRACT = "select#extract"                # populates after CV selected
SELECTOR_COURT = "select#court"                    # value="300" = All Courts
SELECTOR_CASETYPE = "select#casetype"              # populates after extract selected
SELECTOR_FORMAT = "select#format"                  # value="csv"
SELECTOR_FDATE = "input#fdate"
SELECTOR_TDATE = "input#tdate"
SELECTOR_SUBMIT = "input#submitBtn"                # type="button", needs JS click

# Dropdown values (confirmed)
COURT_ALL = "300"
FORMAT_CSV = "csv"

# ---------------------------------------------------------------------------
# Confirmed CSV field names (verified 2026-05-01)
# Note: several headers have trailing spaces — strip when reading
# ---------------------------------------------------------------------------
F_CASE_NUMBER = "Case Number"
F_CASE_TYPE = "Case Type"            # "Eviction" — used to filter if extract has mixed types
F_FILE_DATE = "Case File Date"       # MM/DD/YYYY
F_STYLE = "Style Of Case "           # trailing space — "Plaintiff vs. Defendant"
F_CAUSE = "Cause of Action"          # "Nonpayment - Residential" / "Nonpayment - Commercial"
F_CLAIM_AMOUNT = "Claim Amount"      # rent amount as string e.g. "1896.0000"
F_PLAINTIFF = "Plaintiff Name"       # landlord
F_DEF_NAME = "Defendant Name"        # tenant (may include "And All Other Occupants")
F_DEF_ADDR1 = "Defendant Addr Line 1 "
F_DEF_ADDR2 = "Defendant Addr Line 2 "
F_DEF_CITY = "Defendant Addr City "
F_DEF_STATE = "Defendant Addr State"
F_DEF_ZIP = "Defendant Addr Zip"
F_HEARING_DATE = "Next Hearing Date"

_OCCUPANTS_RE = re.compile(
    r",?\s*(And\s+All\s+Other\s+Occupants?|et\s+al\.?)\s*$",
    re.IGNORECASE,
)


class HarrisCountyScraper(BaseScraper):
    """
    Downloads the Harris County JP eviction CSV extract for the target date
    range and parses it into Filing objects.

    The extract already contains Cause of Action ("Nonpayment - Residential" /
    "Nonpayment - Commercial") and Claim Amount, so the router can make the
    Texas threshold decision without a BatchData call when these fields are
    present.
    """

    def __init__(self, headless: bool = True, lookback_days: int = 1):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        today = court_today(COURT_TIMEZONE)
        start = today - timedelta(days=self.lookback_days)
        start_str = start.strftime("%m/%d/%Y")
        end_str = today.strftime("%m/%d/%Y")

        try:
            log.info(f"Harris County: fetching extract {start_str} → {end_str}")
            await page.goto(PORTAL_URL, wait_until="networkidle")

            # Step 1 — select Civil
            await page.click(SELECTOR_RADIO_CIVIL)
            await page.wait_for_timeout(800)

            # Step 2 — pick extract type (first non-placeholder option after CV loads)
            extract_opts = await page.eval_on_selector_all(
                f"{SELECTOR_EXTRACT} option",
                "els => els.map(o => ({value: o.value, text: o.innerText}))",
            )
            eviction_val = next(
                (o["value"] for o in extract_opts if o["value"] != "0"),
                None,
            )
            if not eviction_val:
                raise RuntimeError("No extract options loaded after selecting Civil")
            await page.select_option(SELECTOR_EXTRACT, value=eviction_val)
            await page.wait_for_timeout(600)

            # Step 3 — All Courts
            await page.select_option(SELECTOR_COURT, value=COURT_ALL)
            await page.wait_for_timeout(400)

            # Step 4 — Case type: pick first available (Eviction only extract)
            casetype_opts = await page.eval_on_selector_all(
                f"{SELECTOR_CASETYPE} option",
                "els => els.map(o => ({value: o.value, text: o.innerText}))",
            )
            ct_val = next(
                (o["value"] for o in casetype_opts if o["text"].strip().lower() == "eviction"),
                "0",
            )
            if ct_val == "0":
                raise RuntimeError("Eviction case type option not found")
            await page.select_option(SELECTOR_CASETYPE, value=ct_val)
            await page.wait_for_timeout(300)

            # Step 5 — CSV format
            await page.select_option(SELECTOR_FORMAT, value=FORMAT_CSV)

            # Step 6 — date range
            await page.fill(SELECTOR_FDATE, start_str)
            await page.fill(SELECTOR_TDATE, end_str)

            # Step 7 — submit (type="button", must use click not form submit)
            async with page.expect_download(timeout=60_000) as dl_info:
                await page.click(SELECTOR_SUBMIT)

            download: Download = await dl_info.value
            csv_text = await self._read_download(download)
            filings = self._parse_csv(csv_text)

        except Exception as e:
            log.error(f"Harris County scrape failed: {e}", exc_info=True)
        finally:
            await self._close_browser()

        log.info(f"Harris County returned {len(filings)} filings")
        return filings

    # ------------------------------------------------------------------

    @staticmethod
    async def _read_download(download: Download) -> str:
        tmp = Path(await download.path())
        text = tmp.read_text(encoding="utf-8", errors="replace")
        tmp.unlink(missing_ok=True)
        return text

    def _parse_csv(self, csv_text: str) -> list[Filing]:
        filings: list[Filing] = []
        # Strip BOM if present
        csv_text = csv_text.lstrip("﻿")
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            try:
                # The extract may include non-eviction civil cases
                if row.get(F_CASE_TYPE, "").strip().lower() != "eviction":
                    continue

                case_number = row[F_CASE_NUMBER].strip()
                filing_date = self._parse_date(row[F_FILE_DATE].strip())

                landlord = row.get(F_PLAINTIFF, "").strip()
                tenant = self._clean_defendant(row.get(F_DEF_NAME, "").strip())

                address = self._build_address(
                    row.get(F_DEF_ADDR1, "").strip(),
                    row.get(F_DEF_ADDR2, "").strip(),
                    row.get(F_DEF_CITY, "").strip(),
                    row.get(F_DEF_STATE, "").strip(),
                    row.get(F_DEF_ZIP, "").strip(),
                )

                hearing_raw = row.get(F_HEARING_DATE, "").strip()
                court_date = self._parse_date(hearing_raw) if hearing_raw else None

                notice_type = row.get(F_CAUSE, "Forcible Detainer").strip()

                claim_amount = self._parse_claim_amount(
                    row.get(F_CLAIM_AMOUNT, "").strip()
                )
                property_type_hint = self._parse_property_type(notice_type)

                filings.append(Filing(
                    case_number=case_number,
                    tenant_name=tenant,
                    property_address=address,
                    landlord_name=landlord,
                    filing_date=filing_date,
                    court_date=court_date,
                    state=STATE,
                    county=COUNTY,
                    notice_type=notice_type,
                    source_url=SOURCE_URL,
                    claim_amount=claim_amount,
                    property_type_hint=property_type_hint,
                ))
            except Exception as e:
                log.warning(f"Skipped row {row.get(F_CASE_NUMBER, '?')}: {e}")
                continue

        return filings

    @staticmethod
    def _clean_defendant(name: str) -> str:
        return _OCCUPANTS_RE.sub("", name).strip().strip(",").strip()

    @staticmethod
    def _build_address(line1: str, line2: str, city: str, state: str, zip_: str) -> str:
        parts = [p for p in [line1, line2] if p]
        if city:
            parts.append(city)
        if state and zip_:
            parts.append(f"{state} {zip_}")
        elif state:
            parts.append(state)
        return ", ".join(parts)

    @staticmethod
    def _parse_claim_amount(raw: str) -> float | None:
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    @staticmethod
    def _parse_property_type(cause: str) -> str | None:
        cause_lower = cause.lower()
        if "commercial" in cause_lower:
            return "commercial"
        if "residential" in cause_lower:
            return "residential"
        return None

    @staticmethod
    def _parse_date(raw: str) -> date:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {raw!r}")
