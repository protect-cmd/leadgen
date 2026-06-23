"""Cosner Drake reuses the Vantage 'Cases Filed' parser, swapping the case type
to Debt Claim and dropping the eviction-only row filter. These tests pin the
case-type parameterization on HarrisCountyScraper and the debt-claim subclass."""
from scrapers.texas.harris import HarrisCountyScraper
from scrapers.texas.harris_debt_claims import (
    HarrisDebtClaimScraper,
    CASE_TYPE,
    EXTRACT_TEXT,
    TX_ANSWER_WINDOW_DAYS,
)

# A mixed Cases-Filed extract: one Debt Claim row (individual defendant, full
# home address) and one Eviction row. Column headers mirror the confirmed
# Harris JP Cases Filed extract (trailing spaces on several fields).
_CSV = (
    "Case Number,Case Type,Case File Date,Style Of Case ,Cause of Action,"
    "Claim Amount,Plaintiff Name,Defendant Name,Defendant Addr Line 1 ,"
    "Defendant Addr Line 2 ,Defendant Addr City ,Defendant Addr State,"
    "Defendant Addr Zip,Next Hearing Date\n"
    "1234567890,Debt Claim,06/12/2026,LVNV vs Rodriguez,Debt Claim,"
    "2450.0000,LVNV Funding LLC,Francisco Rodriguez,123 Main St,,Houston,TX,77002,\n"
    "9876543210,Eviction,06/12/2026,Acme vs Nguyen,Nonpayment - Residential,"
    "1896.0000,Acme Properties,Linda Nguyen,9 Oak Ave,,Houston,TX,77003,06/30/2026\n"
)


def test_debt_claim_constants():
    assert CASE_TYPE == "debt claim"
    assert EXTRACT_TEXT == "cases filed"
    assert TX_ANSWER_WINDOW_DAYS == 30


def test_subclass_configures_cases_filed_debt_claim():
    s = HarrisDebtClaimScraper()
    assert s.casetype == "debt claim"
    assert s.extract_text == "cases filed"


def test_parses_only_debt_claim_rows():
    s = HarrisDebtClaimScraper()
    filings = s._parse_csv(_CSV)
    assert len(filings) == 1
    f = filings[0]
    assert f.case_number == "1234567890"
    assert f.tenant_name == "Francisco Rodriguez"
    assert f.property_address == "123 Main St, Houston, TX 77002"
    assert f.landlord_name == "LVNV Funding LLC"
    assert f.notice_type == "Debt Claim"


def test_eviction_default_unchanged():
    # The legacy Vantage behavior must be preserved: default casetype=eviction
    # keeps only the eviction row from the same mixed extract.
    s = HarrisCountyScraper()
    assert s.casetype == "eviction"
    assert s.extract_text is None
    filings = s._parse_csv(_CSV)
    assert len(filings) == 1
    assert filings[0].tenant_name == "Linda Nguyen"
