from __future__ import annotations

import textwrap
from datetime import date
from unittest.mock import patch

from scrapers.georgia.cobb import CobbMagistrateCourtScraper, _parse_pdf_bytes


# ── PDF parsing unit tests ─────────────────────────────────────────────────


def _fake_pdf_text() -> str:
    return textwrap.dedent("""\
        COBB COUNTY MAGISTRATE COURT
        DISPOSSESSORY CALENDAR
        FRIDAY, MAY 09, 2026 09:00AM
        JUDGE: INMON

        [ 1 ] 26-E-001234   HPA II BORROWER LLC             SMITH J
                         VS
                         DISPOSSESSORY HEARING
                         JOHNSON TENANT
                         AND ALL OCCUPANTS

        [ 2 ] 26-E-001235   JONES PROPERTIES
                         VS
                         MOTION HEARING
                         WILLIAMS ROBERT
    """)


class FakePage:
    height = 792

    def __init__(self, text: str):
        self._text = text

    def crop(self, _bbox):
        return self

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
    assert c1["case_number"] == "26-E-001234"
    assert "HPA II BORROWER" in c1["plaintiff"]
    assert c1["defendant"] == "JOHNSON TENANT"

    c2 = result["cases"][1]
    assert c2["case_number"] == "26-E-001235"
    assert c2["defendant"] == "WILLIAMS ROBERT"


def test_parse_pdf_bytes_skips_all_occupants_line():
    with patch("scrapers.georgia.cobb.pdfplumber.open", return_value=FakePDF(_fake_pdf_text())):
        result = _parse_pdf_bytes(b"fake")
    # "AND ALL OCCUPANTS" must not appear as a defendant
    defendants = [c["defendant"] for c in result["cases"]]
    assert all("OCCUPANTS" not in d.upper() for d in defendants)


def test_parse_pdf_bytes_returns_none_court_date_when_header_missing():
    no_header = "[ 1 ] 26-E-001236   SOME LLC\nVS\nDISPOSSESSORY HEARING\nTENANT NAME\n"
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
