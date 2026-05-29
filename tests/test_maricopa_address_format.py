"""Maricopa property_address formatter - must emit gate-passing strings."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import gates
from scrapers.arizona.maricopa import MaricopaJusticeCourtScraper, MaricopaCaseDetail
from scrapers.arizona.maricopa_assessor import AddressMatchResult, ParcelRecord


def _detail(record: ParcelRecord | None, status: str = "single_match"):
    """Build a minimal MaricopaCaseDetail with the given assessor result."""
    records = [record] if record else []
    if record is None:
        status = "no_match"
    return MaricopaCaseDetail(
        filing_date=date(2026, 5, 25),
        status="OPEN",
        address_match=AddressMatchResult(status=status, records=records),
    )


def _record(address: str, city: str, zip_: str) -> ParcelRecord:
    return ParcelRecord(
        apn="123-45-678",
        owner_name="OWNER",
        physical_address=address,
        mailing_address="",
        physical_city=city,
        physical_zip=zip_,
        jurisdiction="MARICOPA",
    )


def test_single_word_city_formatted_with_commas_and_state():
    record = _record("310 S 3RD AVE AVONDALE 85323", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert result == "310 S 3RD AVE, Avondale, AZ 85323"


def test_multi_word_city_handled_via_structured_field():
    record = _record("100 W MAIN ST QUEEN CREEK 85142", "QUEEN CREEK", "85142")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert result == "100 W MAIN ST, Queen Creek, AZ 85142"


def test_result_passes_gate_address():
    record = _record("310 S 3RD AVE AVONDALE 85323", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert gates.gate_address(result), f"gate_address rejected {result!r}"


def test_no_match_returns_unknown():
    detail = _detail(None)
    assert MaricopaJusticeCourtScraper._property_address(detail) == "Unknown"


def test_empty_record_returns_unknown():
    record = _record("", "", "")
    detail = _detail(record)
    assert MaricopaJusticeCourtScraper._property_address(detail) == "Unknown"


def test_joined_string_does_not_end_with_structured_suffix_returns_raw():
    """Defensive: if the assessor's physical_address doesn't end with the
    structured ' {city} {zip}' suffix, still produce a usable formatted
    string using structured fields rather than dropping the lead."""
    record = _record("DIFFERENT FORMAT 99999", "AVONDALE", "85323")
    detail = _detail(record)
    result = MaricopaJusticeCourtScraper._property_address(detail)
    assert "Avondale" in result
    assert "AZ 85323" in result
