# tests/test_hamilton_judgments.py
from datetime import date

from scrapers.ohio.hamilton_judgments import (
    parse_disposition,
    parse_parties,
    judgment_from_case,
    filter_by_judgment_window,
)
from models.judgment import JudgmentRecord


# --- HTML fixtures (mirror real courtclerk.org table structures) --------------

def _summary(disposition_row: str, *, caption="LOVENESS NDEBELE vs. AMANDA HEMMINGWAY") -> str:
    return f"""
    <table id="case_summary_table">
      <tr><td>Case Number:</td><td>26CV13398</td></tr>
      <tr><td>Case Caption:</td><td>{caption}</td></tr>
      <tr><td>Filed Date:</td><td>04/16/2026</td></tr>
      <tr><td>Case Type:</td><td>G1-EVICTION</td></tr>
      <tr><td>Amount:</td><td></td></tr>
      {disposition_row}
    </table>"""

SUMMARY_JFP = _summary("<tr><td>Disposition:</td><td>06/08/2026 - JUDGMENT FOR PLAINTIFF</td></tr>")
SUMMARY_DISMISSED = _summary("<tr><td>Disposition:</td><td>05/21/2026 - DISMISSED</td></tr>")
SUMMARY_UNDISPOSED = _summary("")  # no Disposition row -> pending

def _party_table(def_name: str, def_addr_html: str) -> str:
    return f"""
    <table id="party_info_table"><tbody>
      <tr><th>Name</th><th>Address</th><th>Party</th><th>Attorney</th></tr>
      <tr><td>LOVENESS NDEBELE</td><td>7322 RIDGE MEADOW CT<br>WEST CHESTER OH 45069</td><td>P&nbsp;1</td><td></td></tr>
      <tr><td>{def_name}</td><td>{def_addr_html}</td><td>D&nbsp;1</td><td></td></tr>
    </tbody></table>"""

PARTY_GOOD = _party_table("AMANDA HEMMINGWAY", "1578 CREST HILL AVE APT #4<br>CINCINNATI OH 45237")
PARTY_AKA = _party_table("KENT ANTHONY MCNEAL II",
                         "AKA KENT MCNEAL II<br>2506 CANTERBURY AVE UNIT #1<br>CINCINNATI OH 45237")
PARTY_ENTITY = _party_table("ACME PROPERTIES LLC", "100 MAIN ST<br>CINCINNATI OH 45202")
PARTY_DOE = _party_table("JOHN DOE", "6920 GLORIA DR<br>CINCINNATI OH 45239")
PARTY_NO_STREET = _party_table("AMANDA HEMMINGWAY", "CINCINNATI OH 45237")  # no street number


# --- parse_disposition --------------------------------------------------------

def test_parse_disposition_judgment_for_plaintiff():
    d, desc, caption = parse_disposition(SUMMARY_JFP)
    assert d == date(2026, 6, 8)
    assert desc == "JUDGMENT FOR PLAINTIFF"
    assert caption == "LOVENESS NDEBELE vs. AMANDA HEMMINGWAY"


def test_parse_disposition_dismissed():
    d, desc, _ = parse_disposition(SUMMARY_DISMISSED)
    assert d == date(2026, 5, 21)
    assert desc == "DISMISSED"


def test_parse_disposition_undisposed_returns_none():
    d, desc, caption = parse_disposition(SUMMARY_UNDISPOSED)
    assert d is None and desc is None
    assert caption == "LOVENESS NDEBELE vs. AMANDA HEMMINGWAY"


# --- parse_parties ------------------------------------------------------------

def test_parse_parties_names_and_address():
    p = parse_parties(PARTY_GOOD)
    assert p["tenant"] == "AMANDA HEMMINGWAY"
    assert p["landlord"] == "LOVENESS NDEBELE"
    assert p["tenant_address"] == "1578 CREST HILL AVE APT #4, CINCINNATI, OH 45237"


def test_parse_parties_skips_alias_line_in_address():
    p = parse_parties(PARTY_AKA)
    # 'AKA KENT MCNEAL II' alias line is skipped; street starts at the digit line.
    assert p["tenant_address"] == "2506 CANTERBURY AVE UNIT #1, CINCINNATI, OH 45237"


# --- judgment_from_case (tenant-lost filter + gates) --------------------------

def test_judgment_from_case_keeps_tenant_lost_with_full_address():
    r = judgment_from_case(SUMMARY_JFP, PARTY_GOOD, case_number="26CV13398")
    assert isinstance(r, JudgmentRecord)
    assert r.state == "OH" and r.county == "Hamilton" and r.window == "W1"
    assert r.defendant_name == "AMANDA HEMMINGWAY"
    assert r.plaintiff_name == "LOVENESS NDEBELE"
    assert r.property_address == "1578 CREST HILL AVE APT #4, CINCINNATI, OH 45237"
    assert r.judgment_date == date(2026, 6, 8)
    assert r.disposition_date == date(2026, 6, 8)
    assert r.disposition_desc == "JUDGMENT FOR PLAINTIFF"


def test_judgment_from_case_drops_dismissed():
    assert judgment_from_case(SUMMARY_DISMISSED, PARTY_GOOD, case_number="X") is None


def test_judgment_from_case_drops_undisposed():
    assert judgment_from_case(SUMMARY_UNDISPOSED, PARTY_GOOD, case_number="X") is None


def test_judgment_from_case_drops_entity_defendant():
    assert judgment_from_case(SUMMARY_JFP, PARTY_ENTITY, case_number="X") is None


def test_judgment_from_case_drops_placeholder_name():
    assert judgment_from_case(SUMMARY_JFP, PARTY_DOE, case_number="X") is None


def test_judgment_from_case_drops_address_without_street():
    assert judgment_from_case(SUMMARY_JFP, PARTY_NO_STREET, case_number="X") is None


# --- filter_by_judgment_window -----------------------------------------------

def test_filter_by_judgment_window_keeps_only_in_range():
    recs = [
        JudgmentRecord(case_number="A", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 8)),   # ref today 06/26 -> 18d old
        JudgmentRecord(case_number="B", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 25)),  # 1d old -> below floor
        JudgmentRecord(case_number="C", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 5, 1)),   # 56d old -> above ceiling
        JudgmentRecord(case_number="D", defendant_name="x", property_address="y",
                       judgment_date=None),               # undated -> dropped
    ]
    kept = {r.case_number for r in filter_by_judgment_window(
        recs, today=date(2026, 6, 26), floor_days=3, ceiling_days=30)}
    assert kept == {"A"}
