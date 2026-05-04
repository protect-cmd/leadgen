from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from models.filing import Filing
from scrapers.base_scraper import BaseScraper

log = logging.getLogger(__name__)

SEARCH_URL = "https://publicindex.sccourts.org/Richland/PublicIndex/PISearch.aspx"
DETAIL_BASE = "https://publicindex.sccourts.org/Richland/PublicIndex/"

STATE = "SC"
COUNTY = "Richland"

_EVICTION_KEYWORDS = {"rule to vacate", "possession", "distress for rent", "ejectm", "notice to quit"}

_JS_PARSE_TABLE = """
() => {
    const rows = [];
    const table = document.querySelector('#ContentPlaceHolder1_SearchResults') ||
                  document.querySelector('[id*="SearchResults"]') ||
                  document.querySelector('table');
    if (!table) return rows;
    const trs = Array.from(table.querySelectorAll('tr')).slice(1);
    for (const tr of trs) {
        const tds = Array.from(tr.querySelectorAll('td'));
        if (tds.length < 8) continue;
        const link = tds[2].querySelector('a');
        rows.push({
            name: tds[0].innerText.trim(),
            partyType: tds[1].innerText.trim(),
            caseNumber: tds[2].innerText.trim(),
            filedDate: tds[3].innerText.trim(),
            subtype: tds[7] ? tds[7].innerText.trim() : '',
            courtAgency: tds[9] ? tds[9].innerText.trim() : '',
            detailHref: link ? link.getAttribute('href') : '',
        });
    }
    return rows;
}
"""

_JS_EXTRACT_ADDRESS = r"""
() => {
    try {
        const text = document.body.innerText;
        const m = text.match(/\d+\s+\w[\w .]+(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Dr(?:ive)?|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl(?:ace)?|Pkwy|Parkway)[^\n]{0,60}/i);
        if (m) return m[0].trim();
    } catch(e) {}
    return null;
}
"""


class RichlandSCScraper(BaseScraper):
    """
    Scrapes Richland County SC Fifth Judicial Circuit Public Index for eviction
    filings (Rule to Vacate / Summary Ejectment). Uses Playwright form interaction
    against the CMSWeb ASP.NET portal — no API key required.
    """

    def __init__(self, headless: bool = True, lookback_days: int = 7):
        super().__init__(headless=headless)
        self.lookback_days = lookback_days

    async def scrape(self) -> list[Filing]:
        page = await self._launch_browser()
        filings: list[Filing] = []

        today = date.today()
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            log.info(f"Richland SC: searching {cutoff} → {today}")

            raw_rows = []
            for attempt in range(1, 4):
                await page.goto(SEARCH_URL, wait_until="load", timeout=60_000)
                await page.wait_for_timeout(1500)

                await page.select_option(
                    "#ContentPlaceHolder1_DropDownListDateFilter", value="Filed"
                )
                await page.fill(
                    "#ContentPlaceHolder1_TextBoxDateFrom", cutoff.strftime("%m/%d/%Y")
                )
                await page.fill(
                    "#ContentPlaceHolder1_TextBoxDateTo", today.strftime("%m/%d/%Y")
                )
                await page.click("#ContentPlaceHolder1_ButtonSearch")
                try:
                    await page.wait_for_selector(
                        "#ContentPlaceHolder1_SearchResults td",
                        timeout=30_000,
                    )
                except Exception:
                    await page.wait_for_load_state("load", timeout=30_000)
                await page.wait_for_timeout(1000)

                raw_rows = await page.evaluate(_JS_PARSE_TABLE)
                log.info(f"Richland SC attempt {attempt}: {len(raw_rows)} rows")
                if raw_rows:
                    break
                if attempt < 3:
                    log.warning("Richland SC: 0 rows, retrying in 5s...")
                    await asyncio.sleep(5)


            cases = self._group_by_case(raw_rows)
            log.info(f"Richland SC: {len(cases)} eviction cases after filtering")

            for case in cases:
                try:
                    address = await self._fetch_address(page, case["detail_href"])
                    filings.append(
                        Filing(
                            case_number=case["case_number"],
                            tenant_name=case["defendant"],
                            property_address=address or "Unknown",
                            landlord_name=case["plaintiff"] or "Unknown",
                            filing_date=case["filed_date"],
                            court_date=None,
                            state=STATE,
                            county=COUNTY,
                            notice_type=case["subtype"] or "Summary Ejectment",
                            source_url=(
                                DETAIL_BASE + case["detail_href"].lstrip("/")
                                if case["detail_href"]
                                else SEARCH_URL
                            ),
                        )
                    )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log.warning(f"Case detail failed {case['case_number']}: {e}")

        except Exception as e:
            log.error(f"Richland SC scrape failed: {e}", exc_info=True)
        finally:
            await self._close_browser()

        log.info(f"Richland SC returned {len(filings)} filings")
        return filings

    def _group_by_case(self, rows: list[dict]) -> list[dict]:
        """Group raw table rows by case number, keeping only eviction cases."""
        by_case: dict[str, dict] = {}

        for row in rows:
            subtype_lower = row["subtype"].lower()
            is_eviction = any(kw in subtype_lower for kw in _EVICTION_KEYWORDS)
            case_num = row["caseNumber"]
            party = row["partyType"].lower()
            name = row["name"]

            if case_num not in by_case:
                by_case[case_num] = {
                    "case_number": case_num,
                    "plaintiff": "",
                    "defendant": "",
                    "filed_date": self._parse_date(row["filedDate"]) or date.today(),
                    "subtype": row["subtype"],
                    "court_agency": row["courtAgency"],
                    "detail_href": row["detailHref"],
                    "is_eviction": False,
                }

            entry = by_case[case_num]
            if is_eviction:
                entry["is_eviction"] = True
            if "plaintiff" in party and not entry["plaintiff"]:
                entry["plaintiff"] = name
            if "defendant" in party and not entry["defendant"]:
                entry["defendant"] = name

        return [v for v in by_case.values() if v["is_eviction"] and v["defendant"]]

    async def _fetch_address(self, page, href: str) -> str | None:
        if not href:
            return None
        url = DETAIL_BASE + href.lstrip("/")
        try:
            await page.goto(url, wait_until="load", timeout=30_000)
            await page.wait_for_timeout(1000)
            return await page.evaluate(_JS_EXTRACT_ADDRESS)
        except Exception as e:
            log.warning(f"Address fetch failed {url}: {e}")
            return None

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        if not raw:
            return None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        return None
