# scrapers/texas/harris_judgments.py
"""ISTS sub-project A — Harris JP "Judgments Entered / Eviction" extract.

Pure parser + tenant-lost filter (this section is browser-free and unit-tested).
The Playwright downloader is appended in Task 4. Does NOT modify scrapers/texas/harris.py.

Confirmed column headers (from Task 0 fixture; note trailing spaces on some fields):
    Case Number | Defendant Name | Defendant Addr Line 1 (trailing sp) |
    Defendant Addr Line 2 (trailing sp) | Defendant Addr City (trailing sp) |
    Defendant Addr State (no trailing sp) | Defendant Addr Zip (no trailing sp) |
    Plaintiff Name | Judgment Date (no trailing sp) |
    Judgment In Favor Of (trailing sp) | Judgment Against (trailing sp) |
    Disposition Desc (no trailing sp) | Disposition Date (trailing sp)
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import Download
from scrapers.base_scraper import BaseScraper
from scrapers.dates import court_today

from models.judgment import JudgmentRecord
from pipeline.gates import gate_address, gate_name
from services.name_utils import clean_tenant_name

log = logging.getLogger(__name__)

SOURCE_URL = "https://jpwebsite.harriscountytx.gov/PublicExtracts/search.jsp"

# Column headers — exact strings from Task 0 fixture (trailing spaces matter for matching).
# The _get() fallback handles minor drift between extract revisions.
C_CASE = "Case Number"
C_DEF_NAME = "Defendant Name"
C_DEF_A1 = "Defendant Addr Line 1 "
C_DEF_A2 = "Defendant Addr Line 2 "
C_DEF_CITY = "Defendant Addr City "
C_DEF_STATE = "Defendant Addr State"
C_DEF_ZIP = "Defendant Addr Zip"
C_PLAINTIFF = "Plaintiff Name"
C_JDATE = "Judgment Date"
C_JFAVOR = "Judgment In Favor Of "
C_JAGAINST = "Judgment Against "
C_DISP_DESC = "Disposition Desc"
C_DISP_DATE = "Disposition Date "


def _get(row: dict, key: str) -> str:
    """Tolerates header whitespace drift between extract revisions."""
    if key in row:
        return (row[key] or "").strip()
    for k in row:
        if k.strip() == key.strip():
            return (row[k] or "").strip()
    return ""


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _build_address(a1: str, a2: str, city: str, state: str, zip_: str) -> str:
    street = " ".join(p for p in (a1, a2) if p).strip()
    if not (street and city and state and zip_):
        return ""
    return f"{street}, {city.title()}, {state.upper()} {zip_}"


_AMP_OR_MULTI_RE = re.compile(r"[&]|\bAND\b", re.IGNORECASE)


def _tenant_lost(defendant_name: str, against: str, favor: str) -> bool:
    """True when judgment is AGAINST the defendant (not in their favor)."""
    if not against:
        return False
    cleaned = clean_tenant_name(defendant_name)
    if not cleaned:
        return False
    # Reject multi-party names (e.g. "Evelyn Gallegos &" after trailer strip).
    # clean_tenant_name strips "All Other Occupants" but leaves a bare "&" or "AND".
    if _AMP_OR_MULTI_RE.search(cleaned):
        return False
    last = cleaned.split()[-1].upper()
    against_u = against.upper()
    favor_u = (favor or "").upper()
    return last in against_u and last not in favor_u


def parse_judgments_csv(csv_text: str) -> list[JudgmentRecord]:
    """Parse a Harris Civil 'Judgments Entered/Eviction' CSV and return tenant-lost records."""
    csv_text = csv_text.lstrip("﻿")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(csv_text))
    out: list[JudgmentRecord] = []
    for row in reader:
        try:
            defendant = _get(row, C_DEF_NAME)
            against = _get(row, C_JAGAINST)
            favor = _get(row, C_JFAVOR)
            if not _tenant_lost(defendant, against, favor):
                continue
            if not gate_name(defendant):
                continue
            address = _build_address(
                _get(row, C_DEF_A1), _get(row, C_DEF_A2), _get(row, C_DEF_CITY),
                _get(row, C_DEF_STATE), _get(row, C_DEF_ZIP),
            )
            if not gate_address(address):
                continue
            out.append(JudgmentRecord(
                case_number=_get(row, C_CASE),
                defendant_name=defendant,
                property_address=address,
                plaintiff_name=_get(row, C_PLAINTIFF) or None,
                judgment_date=_parse_date(_get(row, C_JDATE)),
                judgment_in_favor_of=favor or None,
                judgment_against=against or None,
                disposition_desc=_get(row, C_DISP_DESC) or None,
                disposition_date=_parse_date(_get(row, C_DISP_DATE)),
                source_url=SOURCE_URL,
            ))
        except Exception as e:
            log.warning("ISTS Harris: skipped row %s: %s", _get(row, C_CASE) or "?", e)
            continue
    return out


COURT_TIMEZONE = "America/Chicago"
FLOOR_DAYS = 3        # skip judgments newer than this (posting lag buffer)
CEILING_DAYS = 90     # skip judgments older than this (batched publication; ~90d needed)


class HarrisJudgmentScraper(BaseScraper):
    """Downloads the Harris Civil 'Judgments Entered / Eviction' CSV for the
    judgment-date lookback window and returns tenant-lost JudgmentRecords."""

    def __init__(self, headless: bool = True,
                 floor_days: int = FLOOR_DAYS, ceiling_days: int = CEILING_DAYS):
        super().__init__(headless=headless)
        self.floor_days = floor_days
        self.ceiling_days = ceiling_days
        self.last_error: str | None = None

    async def scrape(self) -> list[JudgmentRecord]:  # type: ignore[override]
        self.last_error = None
        page = await self._launch_browser()
        try:
            today = court_today(COURT_TIMEZONE)
            frm = (today - timedelta(days=self.ceiling_days)).strftime("%m/%d/%Y")
            to = (today - timedelta(days=self.floor_days)).strftime("%m/%d/%Y")
            await page.goto(SOURCE_URL, wait_until="networkidle")
            await page.click("input#civil")
            await page.wait_for_timeout(1200)
            await self._select_by_text(page, "select#extract", "judgments entered")
            await page.wait_for_timeout(800)
            await page.select_option("select#court", value="300")
            await page.wait_for_timeout(400)
            await self._select_by_text(page, "select#casetype", "eviction")
            await page.wait_for_timeout(300)
            await page.select_option("select#format", value="csv")
            await page.fill("input#fdate", frm)
            await page.fill("input#tdate", to)
            async with page.expect_download(timeout=300_000) as dl_info:
                await page.click("input#submitBtn", no_wait_after=True)
            download: Download = await dl_info.value
            tmp = Path(await download.path())
            csv_text = tmp.read_text(encoding="utf-8", errors="replace")
            tmp.unlink(missing_ok=True)
            return parse_judgments_csv(csv_text)
        except Exception as e:
            self.last_error = str(e)
            log.error("ISTS Harris judgment scrape failed: %s", e, exc_info=True)
            return []
        finally:
            await self._close_browser()

    @staticmethod
    async def _select_by_text(page, selector: str, want_text: str) -> None:
        opts = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(o => ({value:o.value, text:o.innerText.trim()}))",
        )
        val = next((o["value"] for o in opts if o["text"].lower() == want_text), None)
        if not val:
            raise RuntimeError(f"option '{want_text}' not found in {selector}")
        await page.select_option(selector, value=val)
