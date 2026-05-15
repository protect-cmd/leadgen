from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today

log = logging.getLogger(__name__)

STATE = "GA"
COUNTY = "DeKalb"
COURT_TIMEZONE = "America/New_York"
CALENDARS_URL = "https://dekalbcountymagistratecourt.com/civil-matters/civil-calendars/"

_CASE_RE = re.compile(r"^\s*(\d+)\s+(\d{2}D\d{5})\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{1,2})[.-](\d{1,2})[.-](\d{2,4})\b")
_HEADER_RE = re.compile(
    r"^(?:Magistrate Court Civil Calendar|JUDGE\b|Dispossessory\b|Case Party Attorney|Page \d+ of \d+)",
    re.IGNORECASE,
)
_ATTORNEY_RE = re.compile(r"^(?:Pro Se|[A-Z][a-z]+ [A-Z]\.? [A-Z][a-z]+|[A-Z][a-z]+ [A-Z][a-z]+)$")
_TENANT_PREFIX_RE = re.compile(
    r"^(?:and\s+)?all\s+(?:other\s+)?(?:occupants?|others)\s*;\s*",
    re.IGNORECASE,
)
_OCCUPANT_ONLY_RE = re.compile(
    r"^(?:and\s+)?all\s+(?:other\s+)?(?:occupants?|others)\s*;?$",
    re.IGNORECASE,
)


class DeKalbDispossessoryScraper:
    """Scrapes DeKalb County Magistrate dispossessory PDF calendars."""

    def __init__(self, lookback_days: int = 2, max_cases: int = 200):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.last_error: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def scrape(self) -> list[Filing]:
        self.last_error = None
        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)
        max_date = today + timedelta(days=14)

        try:
            links = self._fetch_calendar_links()
        except Exception as e:
            self.last_error = f"failed to fetch DeKalb calendar page: {e}"
            log.error("DeKalb GA: %s", self.last_error)
            return []

        filings: list[Filing] = []
        seen_cases: set[str] = set()

        for label, pdf_url in links:
            pdf_date = _parse_date_from_label(f"{label} {pdf_url}")
            if pdf_date is not None and not (cutoff <= pdf_date <= max_date):
                continue

            try:
                cases = _parse_pdf_bytes(self._download_pdf(pdf_url))
            except Exception as e:
                log.warning("DeKalb GA: failed to parse %s: %s", pdf_url, e)
                continue

            for case in cases:
                if len(filings) >= self.max_cases:
                    return filings
                case_number = case["case_number"]
                if case_number in seen_cases:
                    continue
                seen_cases.add(case_number)
                court_date = case.get("court_date") or pdf_date or today
                filings.append(
                    Filing(
                        case_number=case_number,
                        tenant_name=case.get("tenant_name") or "Unknown",
                        property_address="Decatur, GA",
                        landlord_name=case.get("landlord_name") or "Unknown",
                        filing_date=court_date,
                        court_date=court_date,
                        state=STATE,
                        county=COUNTY,
                        notice_type="Dispossessory",
                        source_url=pdf_url,
                    )
                )

        log.info("DeKalb GA: %d dispossessory filings found", len(filings))
        return filings

    def _fetch_calendar_links(self) -> list[tuple[str, str]]:
        response = self.session.get(CALENDARS_URL, timeout=30)
        response.raise_for_status()
        return _dispo_links_from_html(response.text)

    def _download_pdf(self, url: str) -> bytes:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.content


def _dispo_links_from_html(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(CALENDARS_URL, a["href"])
        text = " ".join(a.get_text(" ", strip=True).split())
        haystack = f"{text} {href}".lower()
        if not href.lower().endswith(".pdf"):
            continue
        if "dispo" not in haystack and "dispossessory" not in haystack:
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append((text or href.rsplit("/", 1)[-1], href))
    return links


def _parse_date_from_label(label: str) -> date | None:
    match = _DATE_RE.search(label)
    if not match:
        return None
    month, day, year = match.groups()
    year_i = int(year)
    if year_i < 100:
        year_i += 2000
    try:
        return date(year_i, int(month), int(day))
    except ValueError:
        return None


def _parse_pdf_bytes(pdf_bytes: bytes) -> list[dict]:
    cases: list[dict] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            cases.extend(_parse_page_text(text))
    return cases


def _parse_page_text(text: str) -> list[dict]:
    lines = [_clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line and not _HEADER_RE.match(line)]
    court_date = _extract_court_date(lines)
    case_indices = [
        (idx, match.group(2).upper())
        for idx, line in enumerate(lines)
        if (match := _CASE_RE.match(line))
    ]

    cases: list[dict] = []
    for pos, (idx, case_number) in enumerate(case_indices):
        next_idx = case_indices[pos + 1][0] if pos + 1 < len(case_indices) else len(lines)
        landlord_start = _landlord_start_index(lines, idx)
        landlord = _extract_landlord(lines[landlord_start:idx] + lines[idx + 1:next_idx])
        tenant = _extract_tenant(lines[idx + 1:next_idx])
        cases.append(
            {
                "case_number": case_number,
                "landlord_name": landlord or "Unknown",
                "tenant_name": tenant or "Unknown",
                "court_date": court_date,
            }
        )
    return cases


def _landlord_start_index(lines: list[str], case_index: int) -> int:
    for idx in range(case_index - 1, -1, -1):
        line = lines[idx]
        if _CASE_RE.match(line) or line.lower().startswith("comment:") or _line_is_date(line):
            return idx + 1
    return 0


def _extract_court_date(lines: list[str]) -> date | None:
    for line in lines[:8]:
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y"):
            try:
                return datetime.strptime(line.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _line_is_date(line: str) -> bool:
    return _extract_court_date([line]) is not None


def _extract_landlord(block: list[str]) -> str:
    landlord_parts: list[str] = []
    for line in block:
        if _CASE_RE.match(line):
            break
        if "--- versus ---" in line.lower():
            break
        if "magistrate dispossessory" in line.lower():
            continue
        if line.lower() == "payment of rent":
            continue
        if line.lower().startswith("comment:"):
            landlord_parts.clear()
            continue
        if _looks_like_attorney(line):
            continue
        if _OCCUPANT_ONLY_RE.match(line) or _TENANT_PREFIX_RE.match(line):
            continue
        landlord_parts.append(line)
    return _clean_party(" ".join(landlord_parts))


def _extract_tenant(block: list[str]) -> str:
    tenant_parts: list[str] = []
    after_versus = False
    for line in block:
        lower = line.lower()
        if "--- versus ---" in lower:
            after_versus = True
            continue
        if not after_versus:
            continue
        if line.lower().startswith("comment:"):
            break
        if lower == "payment of rent":
            continue
        if "magistrate dispossessory" in lower:
            continue
        if line.lower() == "pro se":
            continue
        if _OCCUPANT_ONLY_RE.match(line):
            continue
        tenant_parts.append(_TENANT_PREFIX_RE.sub("", line).strip())
    return _clean_party(" ".join(p for p in tenant_parts if p))


def _looks_like_attorney(line: str) -> bool:
    return bool(_ATTORNEY_RE.match(line.strip()))


def _clean_party(raw: str) -> str:
    text = re.sub(r"\bPro Se\b", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"\bet al\.?\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*;\s*", "; ", text)
    return re.sub(r"\s+", " ", text).strip(" ;,")


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()
