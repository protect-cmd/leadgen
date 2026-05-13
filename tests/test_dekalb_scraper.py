from __future__ import annotations

import textwrap
from datetime import date
from unittest.mock import patch

from scrapers.georgia.dekalb import (
    DeKalbDispossessoryScraper,
    _dispo_links_from_html,
    _parse_date_from_label,
    _parse_pdf_bytes,
)


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


def _fake_pdf_text() -> str:
    return textwrap.dedent(
        """\
        Magistrate Court Civil Calendar 1:00 PM
        05/12/2026
        JUDGE Berryl A. Anderson
        Dispossessory
        Dispossessory Virtual Calendar (Attorney)
        Case Party Attorney
        Amani Place Apartments, Columbia
        1 26D08231
        Residential
        Mario Breedlove
        Magistrate Dispossessory - Non
        --- versus ---
        Payment of Rent
        all others; Gloria Gooden Pro Se
        Comment:
        THE VIEW @ STONECREST LLC & 37TV.COM
        2 26D08278
        LLC The View at Stonecrest
        Ashlyn Martin
        Magistrate Dispossessory - Non
        --- versus ---
        Payment of Rent
        And All Other Occupants;
        Pro Se
        Madisen Vickery
        Comment:
        Page 1 of 10
        """
    )


def test_dispo_links_from_html_filters_pdf_calendar_links():
    html = """
    <a href="https://example.test/Civil-Dispo-05.12.26-CT2.pdf">Civil Dispo 05.12.26 CT2</a>
    <a href="https://example.test/CIVIL-Default-05.12.26.pdf">Civil Default</a>
    <a href="/wp-content/uploads/2026/05/Civil-Dispossessory-05.13.26.pdf">Calendar</a>
    <a href="/civil-matters/landlord-tenant-dispossessory/">Landlord-Tenant Dispossessory</a>
    """

    links = _dispo_links_from_html(html)

    assert links == [
        ("Civil Dispo 05.12.26 CT2", "https://example.test/Civil-Dispo-05.12.26-CT2.pdf"),
        ("Calendar", "https://dekalbcountymagistratecourt.com/wp-content/uploads/2026/05/Civil-Dispossessory-05.13.26.pdf"),
    ]


def test_parse_date_from_label_supports_dekalb_filename_formats():
    assert _parse_date_from_label("Civil Dispo 05.12.26 CT2 1pm") == date(2026, 5, 12)
    assert _parse_date_from_label("Civil Dispo 05-11-2026 1200-C") == date(2026, 5, 11)
    assert _parse_date_from_label("CIVIL DISPO 5.13.2026 9AM CT2") == date(2026, 5, 13)


def test_parse_pdf_bytes_extracts_dekalb_dispossessory_cases():
    with patch("scrapers.georgia.dekalb.pdfplumber.open", return_value=FakePDF(_fake_pdf_text())):
        cases = _parse_pdf_bytes(b"fake")

    assert cases == [
        {
            "case_number": "26D08231",
            "landlord_name": "Amani Place Apartments, Columbia Residential",
            "tenant_name": "Gloria Gooden",
            "court_date": date(2026, 5, 12),
        },
        {
            "case_number": "26D08278",
            "landlord_name": "THE VIEW @ STONECREST LLC & 37TV.COM LLC The View at Stonecrest",
            "tenant_name": "Madisen Vickery",
            "court_date": date(2026, 5, 12),
        },
    ]


def test_scraper_builds_filings_from_parsed_pdf(monkeypatch):
    scraper = DeKalbDispossessoryScraper(max_cases=1, lookback_days=2)

    monkeypatch.setattr(
        scraper,
        "_fetch_calendar_links",
        lambda: [("Civil Dispo 05.12.26 CT2", "https://example.test/dispo.pdf")],
    )
    monkeypatch.setattr(
        scraper,
        "_download_pdf",
        lambda _url: b"fake",
    )
    monkeypatch.setattr(
        "scrapers.georgia.dekalb._parse_pdf_bytes",
        lambda _pdf: [
            {
                "case_number": "26D08231",
                "landlord_name": "Amani Place Apartments",
                "tenant_name": "Gloria Gooden",
                "court_date": date(2026, 5, 12),
            }
        ],
    )
    monkeypatch.setattr("scrapers.georgia.dekalb.court_today", lambda _tz: date(2026, 5, 13))

    filings = scraper.scrape()

    assert len(filings) == 1
    assert filings[0].case_number == "26D08231"
    assert filings[0].tenant_name == "Gloria Gooden"
    assert filings[0].landlord_name == "Amani Place Apartments"
    assert filings[0].property_address == "Unknown"
    assert filings[0].state == "GA"
    assert filings[0].county == "DeKalb"
