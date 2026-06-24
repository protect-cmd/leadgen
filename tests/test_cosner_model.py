"""Cosner Drake storage model + the Filing -> CosnerFiling mapper.

These pin the defendant/creditor field mapping and the 30-day Answer deadline
(filing_date + 30d) that is the Cosner Drake urgency clock."""
from datetime import date

from models.cosner import CosnerFiling
from models.filing import Filing
from scrapers.texas.harris_debt_claims import to_cosner_filing, TX_ANSWER_WINDOW_DAYS


def _filing(**kw) -> Filing:
    base = dict(
        case_number="261100274020",
        tenant_name="Linda D Jones",
        property_address="8635 Cottage Gate Ln, Houston, TX 77088",
        landlord_name="Republic Finance LLC",
        filing_date=date(2026, 6, 23),
        state="TX",
        county="Harris",
        notice_type="Debt Claim",
        source_url="https://jpwebsite.harriscountytx.gov/PublicExtracts/search.jsp",
    )
    base.update(kw)
    return Filing(**base)


def test_maps_defendant_creditor_address():
    cf = to_cosner_filing(_filing())
    assert cf.case_number == "261100274020"
    assert cf.defendant_name == "Linda D Jones"
    assert cf.defendant_address == "8635 Cottage Gate Ln, Houston, TX 77088"
    assert cf.creditor_name == "Republic Finance LLC"
    assert cf.state == "TX"
    assert cf.county == "Harris"


def test_answer_deadline_is_filing_plus_30():
    cf = to_cosner_filing(_filing(filing_date=date(2026, 6, 23)))
    assert TX_ANSWER_WINDOW_DAYS == 30
    assert cf.filing_date == date(2026, 6, 23)
    assert cf.answer_deadline == date(2026, 7, 23)


def test_to_row_serializes_dates_iso():
    row = to_cosner_filing(_filing()).to_row()
    assert row["case_number"] == "261100274020"
    assert row["defendant_name"] == "Linda D Jones"
    assert row["creditor_name"] == "Republic Finance LLC"
    assert row["filing_date"] == "2026-06-23"
    assert row["answer_deadline"] == "2026-07-23"
    # write-time row must not carry enrichment/outreach columns
    assert "phone" not in row
    assert "enriched_at" not in row
