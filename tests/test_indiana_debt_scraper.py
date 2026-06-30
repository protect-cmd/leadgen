"""Unit tests for the Indiana MyCase debt scraper's pure parsing layer.

Fixtures mirror real Case/CaseSummary JSON captured live on 2026-06-27
(case 49D12-2606-CC-033187, Capital One, N.A. v. TRAI DEARMAN).
"""
from __future__ import annotations

from datetime import date

import pytest

from models.debt_suit import DebtSuit
from scrapers.indiana.mycase_debt import IndianaMyCaseDebtScraper


def _detail(defendant_address: dict | None, *, defendant=True, plaintiff=True) -> dict:
    parties = []
    if defendant:
        parties.append({
            "Connection": 2,
            "Name": "DEARMAN, TRAI",
            "Address": defendant_address,
        })
    if plaintiff:
        parties.append({
            "Connection": 3,
            "Name": "Capital One, N.A.",
            "Address": {"Line1": "2618 East Paris Ave SE", "City": "Grand Rapids",
                        "State": "MI", "Zip": "49546", "Masked": True},
        })
    return {
        "CaseNumber": "49D12-2606-CC-033187",
        "CaseTypeCode": "CC",
        "CaseCategoryKey": "CV",
        "CaseStatus": "Pending",
        "FileDate": "06/17/2026",
        "CourtCode": "D12",
        "CountyCode": "49",
        "Style": "Capital One, N.A. v. TRAI DEARMAN",
        "Parties": parties,
    }


_GOOD_ADDR = {"Line1": "9305 Memorial Park Dr Apt 3B", "Line2": None,
              "City": "Indianapolis", "State": "IN", "Zip": "46216", "Masked": False}


@pytest.fixture
def scraper() -> IndianaMyCaseDebtScraper:
    return IndianaMyCaseDebtScraper()


def test_happy_path_extracts_defendant_lead(scraper):
    suit = scraper._suit_from_detail(_detail(_GOOD_ADDR), {}, "TOK123")
    assert isinstance(suit, DebtSuit)
    # defendant (Connection==2) is the lead, normalized to First Last
    assert suit.defendant_name == "Trai Dearman"
    assert suit.defendant_address == "9305 Memorial Park Dr Apt 3B, Indianapolis, IN 46216"
    # plaintiff is the creditor, never the target
    assert suit.plaintiff_name == "Capital One, N.A."
    assert suit.case_type_code == "CC"
    assert suit.county == "Marion"
    assert suit.state == "IN"
    assert suit.court_code == "D12"
    assert suit.filing_date == date(2026, 6, 17)
    assert suit.case_status == "Pending"
    assert suit.amount is None and suit.amount_kind is None
    assert "TOK123" in suit.source_url


def test_masked_defendant_address_returns_sentinel(scraper):
    masked = dict(_GOOD_ADDR, Masked=True)
    assert scraper._suit_from_detail(_detail(masked), {}, "T") == "masked"


def test_missing_defendant_returns_none(scraper):
    assert scraper._suit_from_detail(_detail(_GOOD_ADDR, defendant=False), {}, "T") is None


def test_address_without_zip_is_rejected(scraper):
    no_zip = {"Line1": "9305 Memorial Park Dr", "City": "Indianapolis",
              "State": "IN", "Zip": "", "Masked": False}
    assert scraper._suit_from_detail(_detail(no_zip), {}, "T") is None


def test_plaintiff_never_becomes_the_lead(scraper):
    # Only a plaintiff present (no Connection==2) -> not a lead.
    assert scraper._suit_from_detail(_detail(None, defendant=False), {}, "T") is None


def test_first_party_selects_by_connection(scraper):
    parties = [
        {"Connection": 3, "Name": "Capital One, N.A."},
        {"Connection": 2, "Name": "DEARMAN, TRAI"},
    ]
    assert scraper._first_party(parties, 2)["Name"] == "DEARMAN, TRAI"
    assert scraper._first_party(parties, 3)["Name"] == "Capital One, N.A."


def test_format_address_requires_street_and_zip(scraper):
    assert scraper._format_address(_GOOD_ADDR).endswith("IN 46216")
    assert scraper._format_address({"City": "Indianapolis", "State": "IN", "Zip": "46216"}) == ""
    assert scraper._format_address({"Line1": "9305 Memorial Park Dr", "City": "x", "State": "IN"}) == ""


def test_normalize_name_last_first_and_business(scraper):
    assert scraper._normalize_name("DEARMAN, TRAI") == "Trai Dearman"
    assert scraper._normalize_name("SCOTT, CARMAN") == "Carman Scott"
    # business / placeholder names collapse to empty (dropped downstream)
    assert scraper._normalize_name("") == ""


def test_is_target_type_cc_only_by_default(scraper):
    assert scraper._is_target_type("CC - Civil Collection") is True
    assert scraper._is_target_type("SC - Small Claims") is False
    assert scraper._is_target_type("EV - Evictions (Small Claims Docket)") is False


def test_small_claims_opt_in():
    sc_scraper = IndianaMyCaseDebtScraper(case_types=("CC", "SC"))
    assert sc_scraper._is_target_type("SC - Small Claims") is True
    assert sc_scraper._is_target_type("EV - Evictions") is False


def test_parse_date_formats(scraper):
    assert scraper._parse_date("06/17/2026") == date(2026, 6, 17)
    assert scraper._parse_date("2026-06-17T00:00:00") == date(2026, 6, 17)
    assert scraper._parse_date(None) is None
    assert scraper._parse_date("garbage") is None


def test_to_row_roundtrip():
    suit = DebtSuit(
        case_number="49D12-2606-CC-033187",
        defendant_name="Trai Dearman",
        defendant_address="9305 Memorial Park Dr Apt 3B, Indianapolis, IN 46216",
        plaintiff_name="Capital One, N.A.",
        filing_date=date(2026, 6, 17),
        county="Marion",
    )
    row = suit.to_row()
    assert row["filing_date"] == "2026-06-17"
    assert row["defendant_name"] == "Trai Dearman"
    assert row["case_type_code"] == "CC"
    assert row["amount"] is None
