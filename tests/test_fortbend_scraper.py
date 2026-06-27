"""Tests for Fort Bend TX JP eviction scraper."""

from scrapers.texas.fortbend import (
    FortBendTXJPScraper,
    EVICTION_KEYWORDS,
    STATE,
    COUNTY,
    NOTICE_TYPE,
    PORTAL_BASE,
)


# ---------- Module constants ----------

def test_module_constants():
    assert STATE == "TX"
    assert COUNTY == "Fort Bend"
    assert NOTICE_TYPE == "Eviction"
    assert "eviction" in EVICTION_KEYWORDS
    assert "forcible entry" in EVICTION_KEYWORDS


def test_scraper_instantiates():
    s = FortBendTXJPScraper()
    assert s.last_error is None


# ---------- filter_evictions ----------

def test_filter_evictions_keeps_eviction_row():
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-12345", "Case Type": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_drops_non_eviction():
    s = FortBendTXJPScraper()
    rows = [
        {"Case Number": "26-CV-100", "Case Type": "Debt Claim"},
        {"Case Number": "26-SC-200", "Case Type": "Small Claims"},
    ]
    assert s.filter_evictions(rows) == []


def test_filter_evictions_matches_forcible_entry_label():
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-456", "Case Type": "Forcible Entry and Detainer"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_handles_flexible_column_names():
    s = FortBendTXJPScraper()
    rows = [{"Case Number": "26-789", "Type": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1
    rows = [{"Case Number": "26-321", "Cause of Action": "Eviction"}]
    assert len(s.filter_evictions(rows)) == 1


def test_filter_evictions_empty_input():
    s = FortBendTXJPScraper()
    assert s.filter_evictions([]) == []


# ---------- _normalize_url ----------

def test_normalize_url_absolute():
    s = FortBendTXJPScraper()
    assert s._normalize_url("https://example.com/foo") == "https://example.com/foo"


def test_normalize_url_root_path():
    s = FortBendTXJPScraper()
    result = s._normalize_url("/PublicAccess/CaseDetail.aspx?CaseID=12345")
    assert result == PORTAL_BASE + "/PublicAccess/CaseDetail.aspx?CaseID=12345"


def test_normalize_url_relative():
    s = FortBendTXJPScraper()
    result = s._normalize_url("CaseDetail.aspx?CaseID=12345")
    assert result == f"{PORTAL_BASE}/PublicAccess/CaseDetail.aspx?CaseID=12345"


def test_normalize_url_empty():
    s = FortBendTXJPScraper()
    assert s._normalize_url("") == ""
    assert s._normalize_url(None) == ""


# ---------- parse_partial_address ----------

def test_partial_address_plain():
    a = FortBendTXJPScraper.parse_partial_address("Richmond TX 77469")
    assert a == {"city": "Richmond", "state": "TX", "zip": "77469"}


def test_partial_address_multiword_city_zip_plus_4():
    a = FortBendTXJPScraper.parse_partial_address("Sugar Land TX 77479-1234")
    assert a["city"] == "Sugar Land"
    assert a["zip"] == "77479-1234"


def test_partial_address_with_commas():
    a = FortBendTXJPScraper.parse_partial_address("Missouri City, TX 77489")
    assert a["city"] == "Missouri City"
    assert a["state"] == "TX"


def test_partial_address_empty():
    assert FortBendTXJPScraper.parse_partial_address("") == {
        "city": "", "state": "", "zip": ""
    }


def test_partial_address_no_zip():
    assert FortBendTXJPScraper.parse_partial_address("No zip here") == {
        "city": "", "state": "", "zip": ""
    }


# ---------- _grab_after_label ----------

def test_grab_after_label_basic():
    s = FortBendTXJPScraper()
    text = "Case Number: 26-EV-001234\nDate Filed: 06/05/2026\nCause of Action: Eviction"
    assert s._grab_after_label(text, [r"Case Number\s*:?\s*([^\n]+)"]) == "26-EV-001234"
    assert s._grab_after_label(text, [r"Date Filed\s*:?\s*([^\n]+)"]) == "06/05/2026"
    assert s._grab_after_label(text, [r"Cause of Action\s*:?\s*([^\n]+)"]) == "Eviction"


def test_grab_after_label_no_match():
    s = FortBendTXJPScraper()
    assert s._grab_after_label("plain text", [r"nope\s*:\s*(.+)"]) == ""


def test_grab_after_label_pattern_fallback():
    """Multiple patterns tried in order, first match wins."""
    s = FortBendTXJPScraper()
    text = "File Date: 06/01/2026"
    result = s._grab_after_label(
        text, [r"Date Filed\s*:?\s*([^\n]+)", r"File Date\s*:?\s*([^\n]+)"]
    )
    assert result == "06/01/2026"


# ---------- parse_petition_address ----------

def test_petition_address_defendant_preferred_over_plaintiff():
    """When both plaintiff and defendant addresses exist, defendant wins."""
    text = """PLAINTIFF: ACME PROPERTIES LLC
1000 Corporate Way, Sugar Land, TX 77479

DEFENDANT: John Doe
4500 Maple St, Missouri City, TX 77489"""
    a = FortBendTXJPScraper.parse_petition_address(text)
    assert a["street"] == "4500 Maple St"
    assert a["city"] == "Missouri City"
    assert a["state"] == "TX"
    assert a["zip"] == "77489"


def test_petition_address_apt_unit():
    text = "DEFENDANT: Jane Smith\n1234 Cherry Ln Apt 5B, Stafford, TX 77477"
    a = FortBendTXJPScraper.parse_petition_address(text)
    assert a["street"] == "1234 Cherry Ln Apt 5B"
    assert a["city"] == "Stafford"


def test_petition_address_no_commas_zip_plus_4():
    """Regression: street/city without comma + zip+4 (previously failed)."""
    text = "DEFENDANT: Test Tenant\n5500 Oak Drive Richmond TX 77469-1234"
    a = FortBendTXJPScraper.parse_petition_address(text)
    assert a["street"] == "5500 Oak Drive"
    assert a["city"] == "Richmond"
    assert a["zip"] == "77469-1234"


def test_petition_address_multiword_city_no_commas():
    """Regression: multi-word city (Sugar Land) without comma delimiters."""
    text = "DEFENDANT: Sample\n9876 Plantation Blvd Sugar Land TX 77479"
    a = FortBendTXJPScraper.parse_petition_address(text)
    assert a["street"] == "9876 Plantation Blvd"
    assert a["city"] == "Sugar Land"


def test_petition_address_no_match():
    a = FortBendTXJPScraper.parse_petition_address("No address here at all")
    assert a["street"] == "" and a["zip"] == ""


def test_petition_address_empty():
    a = FortBendTXJPScraper.parse_petition_address("")
    assert a == {"street": "", "city": "", "state": "", "zip": "", "raw": ""}


# ---------- extract_petition_text ----------

def test_extract_petition_text_empty_bytes():
    """Empty PDF bytes should return empty string, not raise."""
    assert FortBendTXJPScraper.extract_petition_text(b"") == ""


def test_extract_petition_text_invalid_pdf():
    """Invalid PDF bytes should return empty string gracefully."""
    assert FortBendTXJPScraper.extract_petition_text(b"not a real pdf") == ""


# ---------- normalize_filing ----------

def _sample_case_detail():
    return {
        "source_url": "https://tylerpaw.../CaseDetail.aspx?CaseID=123",
        "case_number": "26-CCV-001234",
        "court": "JP Court 1",
        "filed_date": "06/05/2026",
        "cause_of_action": "Eviction",
        "plaintiff_name": "ACME PROPERTIES LLC",
        "defendant_name": "John Doe",
        "defendant_city": "Sugar Land",
        "defendant_state": "TX",
        "defendant_zip": "77479",
        "petition_url": "https://tylerpaw.../ViewDocumentFragment.aspx?...",
    }


def test_normalize_filing_petition_precedence():
    """Petition address should override case detail's partial address."""
    s = FortBendTXJPScraper()
    petition_addr = {
        "street": "4500 Maple St",
        "city": "Missouri City",
        "state": "TX",
        "zip": "77489",
    }
    f = s.normalize_filing(_sample_case_detail(), petition_addr)
    assert f["state"] == "TX"
    assert f["county"] == "Fort Bend"
    assert f["notice_type"] == "Eviction"
    assert f["case_number"] == "26-CCV-001234"
    assert f["defendant_address_line1"] == "4500 Maple St"
    assert f["defendant_city"] == "Missouri City"  # petition wins
    assert f["plaintiff_name"] == "ACME PROPERTIES LLC"
    assert f["judicial_officer"] == ""
    assert f["precinct"] == ""


def test_normalize_filing_no_petition_falls_back():
    """No petition: street empty, city/state/zip from case detail."""
    s = FortBendTXJPScraper()
    f = s.normalize_filing(_sample_case_detail(), None)
    assert f["defendant_address_line1"] == ""
    assert f["defendant_city"] == "Sugar Land"  # case detail
    assert f["defendant_state"] == "TX"
    assert f["defendant_zip"] == "77479"


def test_normalize_filing_empty_case_detail():
    """Empty case detail should produce a valid skeleton filing."""
    s = FortBendTXJPScraper()
    f = s.normalize_filing({}, None)
    assert f["state"] == "TX" and f["county"] == "Fort Bend"
    assert f["case_number"] == ""
    assert f["defendant_address_line1"] == ""


def test_normalize_filing_schema_completeness():
    """Filing dict must have exactly the 18 expected schema fields."""
    s = FortBendTXJPScraper()
    f = s.normalize_filing(_sample_case_detail(), None)
    expected = {
        "state", "county", "notice_type", "case_number", "court",
        "judicial_officer", "precinct", "filed_date", "hearing_date",
        "cause_of_action", "plaintiff_name", "defendant_name",
        "defendant_address_line1", "defendant_city", "defendant_state",
        "defendant_zip", "judgment_amount", "source_url",
    }
    assert set(f.keys()) == expected


# ---------- dedupe_by_case_number ----------

def test_dedupe_keeps_first_occurrence():
    s = FortBendTXJPScraper()
    filings = [
        {"case_number": "26-001", "defendant_name": "A"},
        {"case_number": "26-002", "defendant_name": "B"},
        {"case_number": "26-001", "defendant_name": "A duplicate"},
        {"case_number": "26-003", "defendant_name": "C"},
    ]
    d = s.dedupe_by_case_number(filings)
    assert len(d) == 3
    assert d[0]["defendant_name"] == "A"


def test_dedupe_drops_empty_or_none():
    s = FortBendTXJPScraper()
    filings = [
        {"case_number": "26-001", "x": 1},
        {"case_number": "", "x": 2},
        {"case_number": None, "x": 3},
        {"case_number": "26-002", "x": 4},
    ]
    d = s.dedupe_by_case_number(filings)
    assert len(d) == 2