# Cobb County GA Magistrate Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready scraper for Cobb County GA Magistrate Court DISPO PDF calendars that parses eviction cases, enriches with property addresses via the Cobb County Parcel MapServer + Nominatim geocoder, and pipes single-match leads through the existing BatchData / GHL / Bland pipeline.

**Architecture:** The scraper fetches DISPO PDF links from the public judicial calendar page, parses each PDF with pdfplumber (same pattern as Davidson TN), then calls the Cobb County ArcGIS Parcel MapServer with the landlord name (OWNER_NAM1 LIKE) to resolve a property address. Since the parcel layer returns street-only `SITUS_ADDR`, a Nominatim geocoder resolves the Cobb County city + ZIP. The complete address goes to BatchData skip-trace. Only `single_match` cases enter the pipeline (same filtering logic as Arizona / Maricopa).

**Tech Stack:** Python 3.11+, requests, pdfplumber, BeautifulSoup4 (calendar page parsing), pytest, pytest-asyncio. No Playwright — all sources are plain HTTP.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `scrapers/georgia/cobb_assessor.py` | Parcel MapServer OWNER_NAM1 LIKE → SITUS_ADDR + PIN |
| Create | `services/nominatim_service.py` | Nominatim OSM geocoder: street → city + ZIP for Cobb County GA |
| Create | `scrapers/georgia/cobb.py` | DISPO PDF scraper: calendar discovery → PDF fetch → pdfplumber parse → assessor lookup → Filing list |
| Create | `jobs/run_georgia_cobb.py` | Job runner: CobbRunSummary, --pipe, --notify, --max-cases, --lookback-days |
| Create | `tests/test_cobb_assessor.py` | Unit tests for CobbAssessorClient |
| Create | `tests/test_nominatim_service.py` | Unit tests for NominatimGeocoder |
| Create | `tests/test_cobb_scraper.py` | Unit tests for _parse_pdf_bytes and calendar link parsing |
| Create | `tests/test_run_georgia_cobb.py` | Unit tests for build_summary and main() |
| Modify | `scripts/smoke_scrapers.py` | Add Cobb scraper to SCRAPER_FACTORIES + STATE_ALIASES |
| Modify | `services/daily_scheduler.py` | Add georgia_cobb job at 14:00 UTC with --pipe --notify |
| Modify | `tests/test_smoke_scrapers.py` | Extend smoke test to cover new cobb factory |
| Modify | `tests/test_daily_scheduler.py` | Extend scheduler test to cover georgia_cobb job |

---

## Task 1: Cobb County Parcel Assessor Client

**Files:**
- Create: `scrapers/georgia/cobb_assessor.py`
- Create: `tests/test_cobb_assessor.py`

**Background:** The Cobb County ArcGIS Parcel MapServer at `https://gis.cobbcounty.gov/gisserver/rest/services/cobbpublic/Parcels/MapServer/0/query` supports an OWNER_NAM1 LIKE query. Confirmed fields: PIN (parcel ID), OWNER_NAM1, OWNER_NAM2, SITUS_ADDR (street-only, no city/ZIP), OWNER_CITY, OWNER_STAT, OWNER_ZIP (mailing address of owner — may be out-of-state). The server requires a real User-Agent header; empty requests return `{}`.

- [ ] **Step 1.1: Write the failing tests**

```python
# tests/test_cobb_assessor.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from scrapers.georgia.cobb_assessor import CobbAssessorClient, CobbParcelRecord


def _arcgis_response(features: list[dict]) -> dict:
    return {"features": [{"attributes": f} for f in features]}


def _make_feature(
    pin="01001001010",
    owner_nam1="SMITH JOHN",
    situs_addr="123 MAIN ST",
    owner_city="MARIETTA",
    owner_stat="GA",
    owner_zip="30060",
) -> dict:
    return {
        "PIN": pin,
        "OWNER_NAM1": owner_nam1,
        "SITUS_ADDR": situs_addr,
        "OWNER_CITY": owner_city,
        "OWNER_STAT": owner_stat,
        "OWNER_ZIP": owner_zip,
    }


def test_single_match_returns_single_match_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([_make_feature()])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "single_match"
    assert len(result.records) == 1
    assert result.records[0].situs_addr == "123 MAIN ST"
    assert result.records[0].pin == "01001001010"


def test_multiple_matches_returns_ambiguous_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([
        _make_feature(pin="01001001010", situs_addr="123 MAIN ST"),
        _make_feature(pin="01001001020", situs_addr="456 OAK AVE"),
    ])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "ambiguous"
    assert len(result.records) == 2


def test_no_matches_returns_no_match_status():
    client = CobbAssessorClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _arcgis_response([])
    with patch.object(client.session, "get", return_value=fake_response):
        result = client.match_owner("UNKNOWN LLC")
    assert result.status == "no_match"
    assert result.records == []


def test_http_error_returns_error_status():
    client = CobbAssessorClient()
    with patch.object(client.session, "get", side_effect=RuntimeError("timeout")):
        result = client.match_owner("SMITH JOHN")
    assert result.status == "error"
    assert "timeout" in result.error


def test_name_normalization_strips_special_chars():
    from scrapers.georgia.cobb_assessor import _normalize_owner_name
    assert _normalize_owner_name("HPA II BORROWER 2020-1 ML LLC") == "HPA II BORROWER 2020 1 ML LLC"
    assert _normalize_owner_name("  SMITH,  JOHN  ") == "SMITH JOHN"
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```
pytest tests/test_cobb_assessor.py -v
```
Expected: `ModuleNotFoundError: No module named 'scrapers.georgia.cobb_assessor'`

- [ ] **Step 1.3: Implement `scrapers/georgia/cobb_assessor.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import requests

_QUERY_URL = (
    "https://gis.cobbcounty.gov/gisserver/rest/services/cobbpublic/Parcels/MapServer/0/query"
)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; eviction-leadgen/1.0)",
    "Accept": "application/json",
}

AddressMatchStatus = Literal["single_match", "ambiguous", "no_match", "error"]


@dataclass(frozen=True)
class CobbParcelRecord:
    pin: str
    owner_nam1: str
    situs_addr: str
    owner_city: str
    owner_stat: str
    owner_zip: str


@dataclass(frozen=True)
class AddressMatchResult:
    status: AddressMatchStatus
    query_variant: str = ""
    records: list[CobbParcelRecord] = field(default_factory=list)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.records is None:
            object.__setattr__(self, "records", [])


class CobbAssessorClient:
    """No-cost ArcGIS owner-name matcher for Cobb County GA parcel data."""

    def __init__(self, session: requests.Session | None = None, result_limit: int = 25):
        self.session = session or requests.Session()
        self.session.headers.update(_HEADERS)
        self.result_limit = result_limit

    def match_owner(self, landlord_name: str) -> AddressMatchResult:
        try:
            for variant in _owner_search_variants(landlord_name):
                records = self._query_owner(variant)
                if not records:
                    continue
                status: AddressMatchStatus = "single_match" if len(records) == 1 else "ambiguous"
                return AddressMatchResult(status=status, query_variant=variant, records=records)
            return AddressMatchResult(status="no_match")
        except Exception as e:
            return AddressMatchResult(status="error", error=str(e))

    def _query_owner(self, owner_variant: str) -> list[CobbParcelRecord]:
        where = "OWNER_NAM1 LIKE '%{}%'".format(owner_variant.replace("'", "''"))
        params = {
            "f": "json",
            "where": where,
            "outFields": "PIN,OWNER_NAM1,SITUS_ADDR,OWNER_CITY,OWNER_STAT,OWNER_ZIP",
            "returnGeometry": "false",
            "resultRecordCount": self.result_limit,
        }
        r = self.session.get(_QUERY_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return [_parse_record(f.get("attributes", {})) for f in data.get("features", [])]


def _owner_search_variants(name: str) -> list[str]:
    variants: list[str] = []
    for part in re.split(r"[/;]", name):
        v = _normalize_owner_name(part)
        if v and v not in variants:
            variants.append(v)
    if not variants:
        v = _normalize_owner_name(name)
        if v:
            variants.append(v)
    return variants


def _normalize_owner_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 &]+", " ", raw.upper())
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_record(attrs: dict) -> CobbParcelRecord:
    def _clean(v: object) -> str:
        return re.sub(r"\s+", " ", str(v or "")).strip()

    return CobbParcelRecord(
        pin=_clean(attrs.get("PIN")),
        owner_nam1=_clean(attrs.get("OWNER_NAM1")),
        situs_addr=_clean(attrs.get("SITUS_ADDR")),
        owner_city=_clean(attrs.get("OWNER_CITY")),
        owner_stat=_clean(attrs.get("OWNER_STAT")),
        owner_zip=_clean(attrs.get("OWNER_ZIP")),
    )
```

- [ ] **Step 1.4: Run tests to confirm they pass**

```
pytest tests/test_cobb_assessor.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 1.5: Commit**

```
git add scrapers/georgia/cobb_assessor.py tests/test_cobb_assessor.py
git commit -m "feat: add Cobb County GA parcel assessor client"
```

---

## Task 2: Nominatim Geocoder Service

**Files:**
- Create: `services/nominatim_service.py`
- Create: `tests/test_nominatim_service.py`

**Background:** The Cobb County parcel layer returns `SITUS_ADDR` as street-only (e.g. "4555 JAMERSON FOREST PKWY"). BatchData skip-trace requires city + ZIP. Nominatim (OpenStreetMap) resolves a street in a county to city + postcode with no API key. Nominatim policy requires a real `User-Agent`. Rate limit: 1 req/sec — add `time.sleep(1.1)` between sequential calls. Confirmed: `"4555 JAMERSON FOREST PKWY, Cobb County, GA, USA"` → city: Marietta, postcode: 30066.

- [ ] **Step 2.1: Write the failing tests**

```python
# tests/test_nominatim_service.py
from __future__ import annotations

from unittest.mock import patch

from services.nominatim_service import NominatimResult, geocode_street_cobb


def _fake_hit(city: str = "Marietta", postcode: str = "30060") -> list[dict]:
    return [{"address": {"city": city, "postcode": postcode}}]


def _fake_miss() -> list[dict]:
    return []


def test_geocode_returns_city_and_postcode():
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_hit("Marietta", "30066")
        result = geocode_street_cobb("4555 JAMERSON FOREST PKWY")
    assert result is not None
    assert result.city == "Marietta"
    assert result.postcode == "30066"


def test_geocode_returns_none_on_no_hit():
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_miss()
        result = geocode_street_cobb("NONEXISTENT ROAD NOWHERE")
    assert result is None


def test_geocode_returns_none_on_http_error():
    with patch("services.nominatim_service.requests.get", side_effect=RuntimeError("timeout")):
        result = geocode_street_cobb("123 MAIN ST")
    assert result is None


def test_geocode_uses_addressdetails_and_county_suffix():
    """Verify the request encodes the county+state context."""
    with patch("services.nominatim_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = _fake_hit()
        geocode_street_cobb("100 TEST LN")
    call_params = mock_get.call_args[1]["params"]
    assert "Cobb County" in call_params["q"]
    assert call_params["addressdetails"] == 1
```

- [ ] **Step 2.2: Run to confirm they fail**

```
pytest tests/test_nominatim_service.py -v
```
Expected: `ModuleNotFoundError: No module named 'services.nominatim_service'`

- [ ] **Step 2.3: Implement `services/nominatim_service.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "eviction-leadgen/1.0 (contact: dev@evictioncommand.com)"


@dataclass(frozen=True)
class NominatimResult:
    city: str | None
    postcode: str | None


def geocode_street_cobb(street: str) -> NominatimResult | None:
    """Geocode a Cobb County GA street address to city + postcode via Nominatim OSM.

    Caller is responsible for rate-limiting (Nominatim policy: 1 req/sec).
    Returns None if the address cannot be resolved.
    """
    query = f"{street}, Cobb County, GA, USA"
    try:
        r = requests.get(
            _NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            log.debug("Nominatim: no result for %r", street)
            return None
        addr = hits[0].get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("suburb")
        )
        postcode = addr.get("postcode")
        return NominatimResult(city=city, postcode=postcode)
    except Exception:
        log.warning("Nominatim geocode failed for %r", street)
        return None
```

- [ ] **Step 2.4: Run tests to confirm they pass**

```
pytest tests/test_nominatim_service.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 2.5: Commit**

```
git add services/nominatim_service.py tests/test_nominatim_service.py
git commit -m "feat: add Nominatim geocoder service for Cobb County GA street lookup"
```

---

## Task 3: Cobb DISPO PDF Scraper

**Files:**
- Create: `scrapers/georgia/cobb.py`
- Create: `tests/test_cobb_scraper.py`

**Background:**
- Calendar page: `https://judicial.cobbcounty.gov/mc/magCalendars/` — lists `<a href="...">` links for each PDF. Only links containing `"DISPO"` (case-insensitive) are eviction dockets.
- PDF filename format: `01 MAY 2026 DISPO 9 AM INMON.pdf` or `01 MAY 2026 DISPO 130 PM LUMPKIN-DAWSON.pdf`
- PDF header line: `FRIDAY, MAY 08, 2026 09:00AM` — parse court date from this.
- PDF case lines: `{entry_num}  {case_number}  {plaintiff_name}  {attorney}` then `VS` on its own line, then `DISPOSSESSORY HEARING` or `MOTION HEARING`, then defendant name.
- Case number format: `\d{2}[A-Z]{2,3}\d{4,7}` (e.g. `26MD01234`)
- `filing_date` is not in the PDF — set to `None`; `court_date` comes from PDF header.
- The scraper enriches addresses via `CobbAssessorClient` + `nominatim_service`. Maintains `address_matches_by_case` and `address_match_counts` dicts (same interface as `MaricopaJusticeCourtScraper`).
- `lookback_days` controls how far back in the calendar to look (calendar has ~30 days of PDFs).

- [ ] **Step 3.1: Write the failing tests**

```python
# tests/test_cobb_scraper.py
from __future__ import annotations

import io
import textwrap
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scrapers.georgia.cobb import CobbMagistrateCourtScraper, _parse_pdf_bytes


# ── PDF parsing unit tests ─────────────────────────────────────────────────


def _fake_pdf_text() -> str:
    return textwrap.dedent("""\
        COBB COUNTY MAGISTRATE COURT
        DISPOSSESSORY CALENDAR
        FRIDAY, MAY 09, 2026 09:00AM
        JUDGE: INMON

        1   26MD001234   HPA II BORROWER LLC             SMITH J
                         VS
                         DISPOSSESSORY HEARING
                         JOHNSON TENANT
                         AND ALL OCCUPANTS

        2   26MD001235   JONES PROPERTIES
                         VS
                         MOTION HEARING
                         WILLIAMS ROBERT
    """)


class FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self, **_kwargs) -> str:
        return self._text


class FakePDF:
    def __init__(self, text: str):
        self.pages = [FakePage(text)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def test_parse_pdf_bytes_extracts_cases_and_court_date():
    with patch("scrapers.georgia.cobb.pdfplumber.open", return_value=FakePDF(_fake_pdf_text())):
        result = _parse_pdf_bytes(b"fake")
    assert result["court_date"] == date(2026, 5, 9)
    assert len(result["cases"]) == 2

    c1 = result["cases"][0]
    assert c1["case_number"] == "26MD001234"
    assert "HPA II BORROWER" in c1["plaintiff"]
    assert c1["defendant"] == "JOHNSON TENANT"

    c2 = result["cases"][1]
    assert c2["case_number"] == "26MD001235"
    assert c2["defendant"] == "WILLIAMS ROBERT"


def test_parse_pdf_bytes_skips_all_occupants_line():
    with patch("scrapers.georgia.cobb.pdfplumber.open", return_value=FakePDF(_fake_pdf_text())):
        result = _parse_pdf_bytes(b"fake")
    # "AND ALL OCCUPANTS" must not appear as a defendant
    defendants = [c["defendant"] for c in result["cases"]]
    assert all("OCCUPANTS" not in d.upper() for d in defendants)


def test_parse_pdf_bytes_returns_none_court_date_when_header_missing():
    no_header = "26MD001236   SOME LLC\nVS\nDISPOSSESSORY HEARING\nTENANT NAME\n"
    with patch("scrapers.georgia.cobb.pdfplumber.open", return_value=FakePDF(no_header)):
        result = _parse_pdf_bytes(b"fake")
    assert result["court_date"] is None


# ── Calendar link parsing ──────────────────────────────────────────────────


def test_scraper_filters_only_dispo_links():
    from scrapers.georgia.cobb import _dispo_links_from_html

    html = """
    <a href="01 MAY 2026 DISPO 9 AM INMON.pdf">DISPO 9 AM</a>
    <a href="01 MAY 2026 ERA 9 AM INMON.pdf">ERA 9 AM</a>
    <a href="01 MAY 2026 DISPO 130 PM LUMPKIN-DAWSON.pdf">DISPO 1:30 PM</a>
    <a href="SMALL CLAIMS 01 MAY 2026.pdf">Small Claims</a>
    """
    links = _dispo_links_from_html(html)
    assert len(links) == 2
    assert all("DISPO" in link for link in links)
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```
pytest tests/test_cobb_scraper.py -v
```
Expected: `ModuleNotFoundError: No module named 'scrapers.georgia.cobb'`

- [ ] **Step 3.3: Implement `scrapers/georgia/cobb.py`**

```python
from __future__ import annotations

import io
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pdfplumber
import requests
from bs4 import BeautifulSoup

from models.filing import Filing
from scrapers.dates import court_today
from scrapers.georgia.cobb_assessor import AddressMatchResult, CobbAssessorClient
from services.nominatim_service import geocode_street_cobb

log = logging.getLogger(__name__)

STATE = "GA"
COUNTY = "Cobb"
COURT_TIMEZONE = "America/New_York"
_CALENDAR_URL = "https://judicial.cobbcounty.gov/mc/magCalendars/"

_COURT_DATE_RE = re.compile(
    r"(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)"
    r",\s+(\w+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
_CASE_RE = re.compile(r"^\s*\d+\s+(\d{2}[A-Z]{2,3}\d{4,7})\s+(.*)", re.IGNORECASE)
_VS_RE = re.compile(r"^\s*VS\s*$", re.IGNORECASE)
_HEARING_TYPE_RE = re.compile(
    r"DISPOSSESSORY\s+HEARING|MOTION\s+HEARING|WRIT\s+HEARING", re.IGNORECASE
)
_OCCUPANTS_RE = re.compile(r"AND\s+ALL\s+OCCUPANTS|ET\s+AL\.?", re.IGNORECASE)


class CobbMagistrateCourtScraper:
    """Scrapes Cobb County GA Magistrate Court DISPO PDF calendars for dispossessory cases."""

    def __init__(
        self,
        lookback_days: int = 30,
        max_cases: int = 200,
        enrich_addresses: bool = True,
    ):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.last_error: str | None = None
        self.address_matches_by_case: dict[str, AddressMatchResult] = {}
        self.address_match_counts: Counter = Counter()

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; eviction-leadgen/1.0)"})
        self._assessor = CobbAssessorClient(session=self._session)

    def scrape(self) -> list[Filing]:
        self.last_error = None
        self.address_matches_by_case.clear()
        self.address_match_counts.clear()

        today = court_today(COURT_TIMEZONE)
        cutoff = today - timedelta(days=self.lookback_days)

        try:
            html = self._session.get(_CALENDAR_URL, timeout=20).text
        except Exception as e:
            self.last_error = f"Failed to fetch calendar page: {e}"
            log.error("Cobb GA: %s", self.last_error)
            return []

        pdf_links = _dispo_links_from_html(html)
        log.info("Cobb GA: found %d DISPO PDF links on calendar page", len(pdf_links))

        filings: list[Filing] = []
        seen_cases: set[str] = set()
        geocode_cache: dict[str, object] = {}

        for link in pdf_links:
            pdf_date = _parse_date_from_filename(link)
            if pdf_date is None or not (cutoff <= pdf_date <= today + timedelta(days=60)):
                continue

            pdf_url = _CALENDAR_URL + link
            log.info("Cobb GA: fetching PDF %s", link)
            try:
                resp = self._session.get(pdf_url, timeout=30)
                resp.raise_for_status()
                parsed = _parse_pdf_bytes(resp.content)
            except Exception as e:
                log.warning("Cobb GA: PDF parse failed for %s: %s", link, e)
                continue

            court_dt = parsed.get("court_date")
            for case in parsed.get("cases", []):
                if len(filings) >= self.max_cases:
                    break
                case_num = case["case_number"]
                if case_num in seen_cases:
                    continue
                seen_cases.add(case_num)

                landlord = case["plaintiff"] or "Unknown"
                tenant = case["defendant"] or "Unknown"
                property_address = "Unknown"

                if self.enrich_addresses and landlord != "Unknown":
                    match = self._assessor.match_owner(landlord)
                    self.address_matches_by_case[case_num] = match
                    self.address_match_counts[match.status] += 1

                    if match.status == "single_match" and match.records:
                        rec = match.records[0]
                        if rec.situs_addr:
                            geo = geocode_cache.get(rec.situs_addr)
                            if geo is None:
                                time.sleep(1.1)  # Nominatim rate limit
                                geo = geocode_street_cobb(rec.situs_addr)
                                geocode_cache[rec.situs_addr] = geo
                            if geo and geo.postcode:
                                city = geo.city or "Marietta"
                                property_address = (
                                    f"{rec.situs_addr}, {city}, GA {geo.postcode}"
                                )
                else:
                    if not self.enrich_addresses:
                        self.address_match_counts["no_match"] += 1

                filings.append(Filing(
                    case_number=case_num,
                    tenant_name=tenant,
                    property_address=property_address,
                    landlord_name=landlord,
                    filing_date=None,
                    court_date=court_dt,
                    state=STATE,
                    county=COUNTY,
                    notice_type="Dispossessory",
                    source_url=pdf_url,
                ))

        log.info("Cobb GA: %d filings found (%d unique)", len(filings), len(seen_cases))
        return filings


def _dispo_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "DISPO" in href.upper():
            links.append(href)
    return links


def _parse_date_from_filename(filename: str) -> date | None:
    """Parse court date from PDF filename: '01 MAY 2026 DISPO 9 AM INMON.pdf'"""
    m = re.match(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", filename, re.IGNORECASE)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2).upper()} {m.group(3)}", "%d %b %Y").date()
    except ValueError:
        return None


def _parse_pdf_bytes(pdf_bytes: bytes) -> dict:
    """Parse a Cobb DISPO PDF. Returns {'court_date': date|None, 'cases': list[dict]}."""
    court_date: date | None = None
    cases: list[dict] = []
    current: dict | None = None
    after_vs = False
    defendant_set = False

    def _finalize() -> None:
        if current and current.get("case_number"):
            cases.append({
                "case_number": current["case_number"],
                "plaintiff": current.get("plaintiff", "").strip(),
                "defendant": current.get("defendant", "").strip(),
            })

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Extract court date from header
                if court_date is None:
                    m = _COURT_DATE_RE.search(line)
                    if m:
                        try:
                            court_date = datetime.strptime(
                                m.group(2).strip(), "%B %d, %Y"
                            ).date()
                        except ValueError:
                            pass
                        continue

                # New case entry
                m = _CASE_RE.match(raw_line)
                if m:
                    _finalize()
                    case_number = m.group(1).upper()
                    plaintiff_raw = re.sub(r"\s{2,}.*$", "", m.group(2)).strip()
                    current = {"case_number": case_number, "plaintiff": plaintiff_raw}
                    after_vs = False
                    defendant_set = False
                    continue

                if current is None:
                    continue

                if _VS_RE.match(line):
                    after_vs = True
                    continue

                if after_vs and _HEARING_TYPE_RE.search(line):
                    continue

                if after_vs and not defendant_set and not _OCCUPANTS_RE.search(line):
                    current["defendant"] = line
                    defendant_set = True

    _finalize()
    return {"court_date": court_date, "cases": cases}
```

- [ ] **Step 3.4: Run tests to confirm they pass**

```
pytest tests/test_cobb_scraper.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 3.5: Commit**

```
git add scrapers/georgia/cobb.py tests/test_cobb_scraper.py
git commit -m "feat: add Cobb County GA Magistrate DISPO PDF scraper"
```

---

## Task 4: Job Runner

**Files:**
- Create: `jobs/run_georgia_cobb.py`
- Create: `tests/test_run_georgia_cobb.py`

- [ ] **Step 4.1: Write the failing tests**

```python
# tests/test_run_georgia_cobb.py
from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from jobs import run_georgia_cobb
from models.filing import Filing
from scrapers.georgia.cobb_assessor import AddressMatchResult


def _filing(case_number: str, property_address: str = "123 Main St, Marietta, GA 30060") -> Filing:
    return Filing(
        case_number=case_number,
        tenant_name="Tenant",
        property_address=property_address,
        landlord_name="Landlord LLC",
        filing_date=None,
        court_date=date(2026, 5, 15),
        state="GA",
        county="Cobb",
        notice_type="Dispossessory",
        source_url="https://example.com",
    )


class FakeCobbScraper:
    def __init__(self, *, lookback_days: int, max_cases: int, enrich_addresses: bool):
        self.lookback_days = lookback_days
        self.max_cases = max_cases
        self.enrich_addresses = enrich_addresses
        self.address_match_counts = Counter({
            "single_match": 1,
            "ambiguous": 1,
            "no_match": 1,
            "error": 0,
        })
        self.address_matches_by_case: dict[str, AddressMatchResult] = {
            "26MD000001": AddressMatchResult(status="single_match"),
            "26MD000002": AddressMatchResult(status="ambiguous"),
            "26MD000003": AddressMatchResult(status="no_match"),
        }

    def scrape(self) -> list[Filing]:
        return [
            _filing("26MD000001", "100 Oak St, Marietta, GA 30060"),
            _filing("26MD000002", "Unknown"),
            _filing("26MD000003", "Unknown"),
        ]


def test_build_summary_counts_single_match_as_usable():
    summary = run_georgia_cobb.build_summary(
        filings=[_filing("26MD000001")],
        address_match_counts=Counter({"single_match": 1, "ambiguous": 2, "no_match": 3}),
        max_cases=100,
        lookback_days=30,
        piped=False,
    )
    assert summary.total_filings == 1
    assert summary.usable_single_match == 1
    assert summary.held_for_review == 5
    lines = summary.to_lines()
    assert "Georgia / Cobb scraper-only proof" in lines[0]
    assert "Runner/enrichment/outreach: not called (scraper-only mode)" in lines[-1]


@pytest.mark.asyncio
async def test_main_scraper_only_mode(monkeypatch, capsys):
    monkeypatch.setattr(run_georgia_cobb, "CobbMagistrateCourtScraper", FakeCobbScraper)
    summary = await run_georgia_cobb.main(max_cases=100, lookback_days=30, notify=False)
    assert summary.total_filings == 3
    assert summary.usable_single_match == 1
    assert not summary.piped
    out = capsys.readouterr().out
    assert "Georgia / Cobb scraper-only proof" in out


@pytest.mark.asyncio
async def test_main_pipe_mode_sends_only_single_match(monkeypatch, capsys):
    piped_filings: list[Filing] = []

    async def fake_run(filings, *, state, county):
        piped_filings.extend(filings)

    monkeypatch.setattr(run_georgia_cobb, "CobbMagistrateCourtScraper", FakeCobbScraper)

    import pipeline.runner as pipeline_runner
    monkeypatch.setattr(pipeline_runner, "run", fake_run)

    summary = await run_georgia_cobb.main(max_cases=100, lookback_days=30, notify=False, pipe=True)

    assert summary.piped is True
    assert len(piped_filings) == 1
    assert piped_filings[0].case_number == "26MD000001"
    out = capsys.readouterr().out
    assert "Georgia / Cobb pipeline run" in out
    assert "Runner: called with 1 single-match filings" in out
```

- [ ] **Step 4.2: Run to confirm they fail**

```
pytest tests/test_run_georgia_cobb.py -v
```
Expected: `ModuleNotFoundError: No module named 'jobs.run_georgia_cobb'`

- [ ] **Step 4.3: Implement `jobs/run_georgia_cobb.py`**

```python
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.filing import Filing
from scrapers.georgia.cobb import CobbMagistrateCourtScraper
from services import notification_service

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CobbRunSummary:
    total_filings: int
    usable_single_match: int
    ambiguous: int
    no_match: int
    errors: int
    max_cases: int
    lookback_days: int
    piped: bool

    @property
    def held_for_review(self) -> int:
        return self.ambiguous + self.no_match + self.errors

    def to_lines(self) -> list[str]:
        runner_line = (
            f"Runner: called with {self.usable_single_match} single-match filings"
            if self.piped
            else "Runner/enrichment/outreach: not called (scraper-only mode)"
        )
        return [
            "Georgia / Cobb" + (" pipeline run" if self.piped else " scraper-only proof"),
            f"Max cases: {self.max_cases}",
            f"Lookback days: {self.lookback_days}",
            f"Total filings: {self.total_filings}",
            f"Usable single-match addresses: {self.usable_single_match}",
            f"Held for review: {self.held_for_review}",
            f"Ambiguous owner matches: {self.ambiguous}",
            f"No owner match: {self.no_match}",
            f"Match errors: {self.errors}",
            runner_line,
        ]


def build_summary(
    *,
    filings: list[Filing],
    address_match_counts: Counter,
    max_cases: int,
    lookback_days: int,
    piped: bool,
) -> CobbRunSummary:
    return CobbRunSummary(
        total_filings=len(filings),
        usable_single_match=int(address_match_counts.get("single_match", 0)),
        ambiguous=int(address_match_counts.get("ambiguous", 0)),
        no_match=int(address_match_counts.get("no_match", 0)),
        errors=int(address_match_counts.get("error", 0)),
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=piped,
    )


async def main(
    *,
    max_cases: int = 200,
    lookback_days: int = 30,
    notify: bool = False,
    pipe: bool = False,
) -> CobbRunSummary:
    log.info("Starting Georgia / Cobb %s", "pipeline run" if pipe else "scraper-only proof")
    scraper = CobbMagistrateCourtScraper(
        lookback_days=lookback_days,
        max_cases=max_cases,
        enrich_addresses=True,
    )
    filings = scraper.scrape()

    if pipe:
        from pipeline import runner as pipeline_runner

        single_match_filings = [
            f for f in filings
            if scraper.address_matches_by_case.get(f.case_number) is not None
            and scraper.address_matches_by_case[f.case_number].status == "single_match"
            and f.property_address not in ("Unknown", "", None)
        ]
        log.info(
            "Cobb GA: passing %d single-match filings to pipeline (%d held)",
            len(single_match_filings),
            len(filings) - len(single_match_filings),
        )
        if single_match_filings:
            await pipeline_runner.run(single_match_filings, state="GA", county="Cobb")
        else:
            log.info("Cobb GA: no single-match filings to pipe")

    summary = build_summary(
        filings=filings,
        address_match_counts=scraper.address_match_counts,
        max_cases=max_cases,
        lookback_days=lookback_days,
        piped=pipe,
    )

    message = "\n".join(summary.to_lines())
    print(message)

    if notify:
        await notification_service.send_alert(
            "Georgia Cobb run",
            message,
            tags={"mode": "pipeline" if pipe else "scraper-only"},
        )

    log.info("Georgia / Cobb run complete")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Georgia / Cobb County Magistrate Court dispossessory scraper. "
            "Default: scraper-only proof. Add --pipe to send single-match filings "
            "through BatchData / GHL / Bland."
        )
    )
    parser.add_argument("--max-cases", type=int, default=200)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--pipe", action="store_true")
    return parser


def cli() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        main(
            max_cases=args.max_cases,
            lookback_days=args.lookback_days,
            notify=args.notify,
            pipe=args.pipe,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
```

- [ ] **Step 4.4: Run tests to confirm they pass**

```
pytest tests/test_run_georgia_cobb.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 4.5: Commit**

```
git add jobs/run_georgia_cobb.py tests/test_run_georgia_cobb.py
git commit -m "feat: add Cobb County GA job runner with CobbRunSummary"
```

---

## Task 5: Smoke Test Registration + Daily Scheduler

**Files:**
- Modify: `scripts/smoke_scrapers.py`
- Modify: `services/daily_scheduler.py`
- Modify: `tests/test_smoke_scrapers.py`
- Modify: `tests/test_daily_scheduler.py`

- [ ] **Step 5.1: Read current smoke_scrapers test**

```
pytest tests/test_smoke_scrapers.py -v
```
Note the existing test names. You'll extend the file, not replace it.

- [ ] **Step 5.2: Add Cobb to `scripts/smoke_scrapers.py`**

Add import at the top (after existing imports):
```python
from scrapers.georgia.cobb import CobbMagistrateCourtScraper
```

Add factory function after `_georgia_scrapers`:
```python
def _georgia_cobb_scrapers(lookback_days: int, headless: bool) -> list[tuple[str, object]]:
    return [("Cobb Magistrate", CobbMagistrateCourtScraper(lookback_days=lookback_days, max_cases=25, enrich_addresses=False))]
```

Add to `SCRAPER_FACTORIES` dict:
```python
"georgia_cobb": _georgia_cobb_scrapers,
```

Add to `STATE_ALIASES` dict:
```python
"georgia_cobb": "georgia_cobb",
"cobb": "georgia_cobb",
"ga_cobb": "georgia_cobb",
```

Update the `--states` help string to include `georgia_cobb, cobb`.

- [ ] **Step 5.3: Add Cobb to `services/daily_scheduler.py`**

In the `SCHEDULED_JOBS` tuple, add after the arizona entry:
```python
ScheduledJob("georgia_cobb", 14, 0, "run_georgia_cobb.py", args=("--pipe", "--notify")),
```

- [ ] **Step 5.4: Write new scheduler test**

Read `tests/test_daily_scheduler.py` first to understand current test structure, then add:

```python
def test_georgia_cobb_job_is_scheduled():
    from services.daily_scheduler import SCHEDULED_JOBS
    names = [j.name for j in SCHEDULED_JOBS]
    assert "georgia_cobb" in names


def test_georgia_cobb_job_has_pipe_and_notify():
    from services.daily_scheduler import SCHEDULED_JOBS
    job = next(j for j in SCHEDULED_JOBS if j.name == "georgia_cobb")
    assert "--pipe" in job.args
    assert "--notify" in job.args
    assert job.script_name == "run_georgia_cobb.py"
```

- [ ] **Step 5.5: Write new smoke test**

Add to `tests/test_smoke_scrapers.py`:

```python
def test_parse_states_recognises_cobb_alias():
    from scripts.smoke_scrapers import parse_states
    assert parse_states("cobb") == ["georgia_cobb"]
    assert parse_states("georgia_cobb") == ["georgia_cobb"]


def test_georgia_cobb_factory_returns_scraper():
    from scripts.smoke_scrapers import SCRAPER_FACTORIES
    scrapers = SCRAPER_FACTORIES["georgia_cobb"](7, True)
    assert len(scrapers) == 1
    label, scraper = scrapers[0]
    assert label == "Cobb Magistrate"
    assert scraper.enrich_addresses is False
```

- [ ] **Step 5.6: Run all new tests**

```
pytest tests/test_daily_scheduler.py tests/test_smoke_scrapers.py -v
```
Expected: all tests PASS (including pre-existing ones).

- [ ] **Step 5.7: Commit**

```
git add scripts/smoke_scrapers.py services/daily_scheduler.py tests/test_smoke_scrapers.py tests/test_daily_scheduler.py
git commit -m "feat: register Cobb GA scraper in smoke tests and daily scheduler at 14:00 UTC"
```

---

## Task 6: Live Smoke Test (Manual Verification)

This task requires network access and a `.env` with `BATCHDATA_API_KEY`. Do not run in CI.

- [ ] **Step 6.1: Run scraper-only smoke (no BatchData)**

```
python scripts/smoke_scrapers.py --states cobb --lookback-days 30
```
Expected output line: `Georgia / cobb_cobb Magistrate: N filings` where N > 0.
If N = 0: verify that `https://judicial.cobbcounty.gov/mc/magCalendars/` has DISPO PDFs for the last 30 days. Calendar rotates — older PDFs may be removed.

- [ ] **Step 6.2: Run proof job with address enrichment**

```
python jobs/run_georgia_cobb.py --max-cases 20 --lookback-days 30
```
Expected: at least some `single_match` lines in output. Typical expectation: 10–30% single-match rate. If `usable_single_match = 0` across 20+ cases, verify the assessor query URL is reachable and OWNER_NAM1 search returns results.

- [ ] **Step 6.3: Run full test suite to confirm no regressions**

```
pytest --ignore=tests/test_cobb_scraper.py -x -q
```
(Excluding `test_cobb_scraper.py` if pdfplumber is slow in the test environment; it uses mocked pdfplumber so it's fine to include.)

```
pytest -x -q
```
Expected: all tests PASS.

- [ ] **Step 6.4: Final commit**

```
git add -A
git commit -m "test: confirm Cobb GA scraper smoke passes and all tests green"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Calendar discovery (DISPO PDF links from magCalendars page)
- [x] pdfplumber PDF parsing (court_date, case_number, plaintiff, defendant)
- [x] CobbAssessorClient (OWNER_NAM1 LIKE → SITUS_ADDR + PIN)
- [x] Nominatim geocoder (SITUS_ADDR → city + ZIP)
- [x] BatchData skip-trace (via existing `enrich()` in pipeline runner — not called in scraper)
- [x] Single-match filtering (only `single_match` piped)
- [x] `address_matches_by_case` + `address_match_counts` on scraper
- [x] `--pipe` flag on job runner
- [x] `--notify` flag on job runner
- [x] `filing_date = None` (not in DISPO PDF)
- [x] De-duplication by case number across multiple PDFs
- [x] Daily scheduler at 14:00 UTC
- [x] Smoke test registration

**Placeholder scan:** None found.

**Type consistency:**
- `AddressMatchResult` in `cobb_assessor.py` matches interface used in `cobb.py` and `run_georgia_cobb.py`
- `CobbRunSummary.to_lines()` matches pattern in `run_arizona.ArizonaRunSummary.to_lines()`
- `build_summary()` signature consistent with test expectations
