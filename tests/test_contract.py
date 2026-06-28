"""Tests for the shared pipeline contract + per-business adapters."""
from __future__ import annotations

from datetime import date

from models.amount_kind import BACK_RENT_TOTAL, DEBT_CLAIM_TOTAL
from models.cosner import CosnerFiling
from models.filing import Filing
from models.garnishment import GarnishmentRecord
from models.judgment import JudgmentRecord
from pipeline.contract import (
    Business,
    from_cosner_filing,
    from_filing,
    from_garnishment,
    from_judgment,
)


def test_from_filing_maps_vantage():
    f = Filing(
        case_number="2026-001",
        tenant_name="JOHN SMITH",
        property_address="123 Main St, Houston TX 77002",
        landlord_name="ACME PROPERTIES",
        filing_date=date(2026, 6, 20),
        court_date=date(2026, 7, 1),
        state="TX",
        county="Harris",
        notice_type="Eviction",
        source_url="http://x",
        claim_amount=1500.0,
    )
    r = from_filing(f)
    assert r.business is Business.VANTAGE
    assert (r.first_name, r.last_name) == ("JOHN", "SMITH")
    assert r.counterparty_name == "ACME PROPERTIES"
    assert r.amount == 1500.0 and r.amount_kind == BACK_RENT_TOTAL
    assert r.freshness_date == date(2026, 6, 20) and r.freshness_kind == "filing_date"
    assert r.deadline_date == date(2026, 7, 1) and r.deadline_kind == "court_date"
    assert r.has_street_address is True
    assert r.dedupe_key == ("vantage", "Harris", "2026-001")


def test_from_filing_no_amount_has_no_kind():
    f = Filing(
        case_number="c", tenant_name="A B", property_address="Unknown",
        landlord_name="L", filing_date=date(2026, 6, 1), state="TX",
        county="Harris", notice_type="Eviction", source_url="u",
    )
    r = from_filing(f)
    assert r.amount is None and r.amount_kind is None
    assert r.has_street_address is False  # "Unknown" placeholder


def test_from_judgment_maps_ists():
    j = JudgmentRecord(
        case_number="J-1", defendant_name="JANE DOE",
        property_address="9 Oak Ave, Houston TX 77003", plaintiff_name="LL CO",
        state="TX", county="Harris", judgment_date=date(2026, 6, 25),
    )
    r = from_judgment(j)
    assert r.business is Business.ISTS
    assert (r.first_name, r.last_name) == ("JANE", "DOE")
    assert r.freshness_kind == "judgment_date"
    assert r.deadline_kind is None  # ISTS has no deadline column


def test_from_cosner_passes_amount_and_deadline():
    c = CosnerFiling(
        case_number="CD-1", defendant_name="BOB JONES",
        defendant_address="5 Elm St, Houston TX 77004", creditor_name="DEBT BUYER",
        filing_date=date(2026, 6, 10), answer_deadline=date(2026, 7, 10),
        debt_amount=4200.0, amount_kind=DEBT_CLAIM_TOTAL,
    )
    r = from_cosner_filing(c)
    assert r.business is Business.COSNER
    assert r.amount == 4200.0 and r.amount_kind == DEBT_CLAIM_TOTAL
    assert r.deadline_kind == "answer_deadline"
    assert r.counterparty_name == "DEBT BUYER"


def test_from_garnishment_splits_last_first_and_anchors_writ():
    g = GarnishmentRecord(
        case_number="09-CC-001607", debtor_name="ROGERS, JAMES",
        debtor_address="6504 SALINE STREET TAMPA FL 33634",
        creditor_name="FINANCIAL PORTFOLIOS II INC", garnishee_name="BANK OF AMERICA",
        state="FL", county="Hillsborough", filing_date=date(2026, 5, 20),
        garnishment_type="wage", exemption_deadline=date(2026, 6, 9),
    )
    r = from_garnishment(g)
    assert r.business is Business.GARNISH_PROOF
    assert (r.first_name, r.last_name) == ("JAMES", "ROGERS")
    assert r.freshness_date == date(2026, 5, 20) and r.freshness_kind == "writ_filed_date"
    assert r.deadline_kind == "exemption_deadline"
    assert r.has_street_address is True
    assert r.dedupe_key == ("garnish_proof", "Hillsborough", "09-CC-001607")
