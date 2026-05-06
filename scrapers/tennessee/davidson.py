from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

import pdfplumber
import requests

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "TN"
COUNTY = "Davidson"
COURT_TIMEZONE = "America/Chicago"

_API_URL = "https://caselink.nashville.gov/cgi-bin/webshell.asp"
_PDF_BASE = "https://caselink.nashville.gov"

_SKIP_DIVISIONS = {"4D"}  # Orders of Protection only

_CASE_RE = re.compile(r"^\s+(\d{2}GT\d+)\s+(?:\((\d+)\)\s+)?(.+?)\s{3,}(.+?)\s*$")
_SEP_RE = re.compile(r"^[-]{5,}$")
# Right column starts at character 60 in the layout-preserved text
_RIGHT_COL = 60
_CITY_STATE_RE = re.compile(r".+,\s*TN\s+\d{5}", re.IGNORECASE)
_HEADER_RE = re.compile(
    r"Davidson County|General Sessions|Court Date:|Court Room|Plaintiff|Defendant|Docket Num|Lawyer|Metropolitan|Civil Division|Page \d"
)


class DavidsonTNScraper:
    """
    Scrapes Davidson County (Nashville) General Sessions Civil dockets for
    eviction filings (GT case prefix = General Tenancy/Detainer Warrant).

    Downloads daily docket PDFs from caselink.nashville.gov, parses them with
    pdfplumber, and returns Filing objects for GT cases only.
    """

    def __init__(self, lookback_days: int = 7):
        self.lookback_days = lookback_days
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            docket_entries = self._fetch_docket_list()
        except Exception as e:
            self.last_error = f"failed to fetch docket list: {e}"
            log.error(f"Davidson TN: failed to fetch docket list: {e}")
            return []

        log.info(f"Davidson TN: {len(docket_entries)} total docket entries available")

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for entry in docket_entries:
            court_date_str, office, division, docket_type, pdf_path = entry

            if division in _SKIP_DIVISIONS:
                continue

            try:
                court_dt = datetime.strptime(court_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if court_dt < cutoff or court_dt > today + timedelta(days=4):
                continue

            pdf_url = _PDF_BASE + pdf_path
            log.info(f"Davidson TN: downloading docket {division} {court_date_str}")

            try:
                cases = self._parse_docket_pdf(pdf_url)
            except Exception as e:
                log.warning(f"Davidson TN: PDF parse failed {pdf_url}: {e}")
                continue

            for case in cases:
                if case["case_number"] in seen_cases:
                    continue
                seen_cases.add(case["case_number"])

                filings.append(Filing(
                    case_number=case["case_number"],
                    tenant_name=case["defendant"] or "Unknown",
                    property_address=case["address"] or "Unknown",
                    landlord_name=case["plaintiff"] or "Unknown",
                    filing_date=court_dt,
                    court_date=court_dt,
                    state=STATE,
                    county=COUNTY,
                    notice_type="Detainer Warrant",
                    source_url=pdf_url,
                ))

        log.info(f"Davidson TN: {len(filings)} eviction filings found")
        return filings

    def _fetch_docket_list(self) -> list[tuple]:
        import time
        t = int(time.time() * 1000)
        xio = t + datetime.now().day

        body = (
            "GATEWAY=GATEWAY&XGATEWAY=SessionsDocketInfo&CGISCRIPT=webshell.asp"
            "&XEVENT=VERIFY&MYPARENT=px&APPID=dav&WEBWORDSKEY=SAMPLE"
            "&DEVPATH=/INNOVISION/DEVELOPMENT/DAVMAIN.DEV&OPERCODE=dummy&PASSWD=dummy"
            f"&WEBIOHANDLE={xio}"
        )
        r = self.session.post(_API_URL, data=body,
                              headers={"Content-Type": "application/x-www-form-urlencoded"},
                              timeout=20)
        r.raise_for_status()
        import json
        data = json.loads(r.text)
        return [tuple(row) for row in data]

    def _parse_docket_pdf(self, pdf_url: str) -> list[dict]:
        r = self.session.get(pdf_url, timeout=30)
        r.raise_for_status()
        return _parse_pdf_bytes(r.content)


def _parse_pdf_bytes(pdf_bytes: bytes) -> list[dict]:
    cases: list[dict] = []
    current: dict | None = None
    right_lines: list[str] = []

    def finalize():
        if current is None:
            return
        address = _extract_address(right_lines)
        cases.append({
            "case_number": current["case_number"],
            "plaintiff": current["plaintiff"],
            "defendant": _clean_defendant(current["first_defendant"]),
            "address": address,
        })

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True)
            if not text:
                continue

            for raw_line in text.splitlines():
                if _HEADER_RE.search(raw_line):
                    continue
                if not raw_line.strip():
                    continue

                m = _CASE_RE.match(raw_line)
                if m:
                    finalize()
                    case_num, _cont, plaintiff, first_def = m.groups()
                    current = {
                        "case_number": case_num,
                        "plaintiff": plaintiff.strip(),
                        "first_defendant": first_def.strip(),
                    }
                    right_lines = []
                    continue

                if current is None:
                    continue

                content = raw_line[_RIGHT_COL:].strip()
                if content:
                    right_lines.append(content)

    finalize()
    return cases


def _extract_address(right_lines: list[str]) -> str:
    """Extract the first defendant's street address + city/state/zip."""
    addr_parts: list[str] = []
    in_address = False

    for line in right_lines:
        if _SEP_RE.match(line.strip()):
            break  # stop at first separator (end of first defendant block)

        if not in_address and re.match(r"^\d+\s+\w", line):
            in_address = True

        if in_address:
            addr_parts.append(line)
            if _CITY_STATE_RE.search(line):
                break

    return ", ".join(addr_parts) if addr_parts else ""


def _clean_defendant(name: str) -> str:
    name = re.sub(r"\s+OR ALL OCCUPANTS.*", "", name, flags=re.IGNORECASE)
    return name.strip()
