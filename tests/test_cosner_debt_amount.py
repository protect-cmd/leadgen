from datetime import date

from models.amount_kind import DEBT_CLAIM_TOTAL
from models.cosner import CosnerFiling
from models.filing import Filing
from scrapers.texas.harris_debt_claims import to_cosner_filing


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


def test_to_cosner_filing_carries_debt_amount_and_kind():
    cf = to_cosner_filing(_filing(claim_amount=1234.56))

    assert cf.debt_amount == 1234.56
    assert cf.amount_kind == DEBT_CLAIM_TOTAL


def test_to_cosner_filing_leaves_amount_fields_none_without_claim_amount():
    cf = to_cosner_filing(_filing(claim_amount=None))

    assert cf.debt_amount is None
    assert cf.amount_kind is None


def test_to_row_includes_amount_fields():
    row = CosnerFiling(
        case_number="261100274020",
        defendant_name="Linda D Jones",
        defendant_address="8635 Cottage Gate Ln, Houston, TX 77088",
        debt_amount=1234.56,
        amount_kind=DEBT_CLAIM_TOTAL,
    ).to_row()

    assert row["debt_amount"] == 1234.56
    assert row["amount_kind"] == DEBT_CLAIM_TOTAL
