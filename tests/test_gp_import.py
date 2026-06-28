"""Tests for the Garnish Proof spreadsheet importer (Phase 7)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from scripts.import_gp_garnishment_xlsx import (
    _EXEMPTION_WINDOW_DAYS,
    _clean,
    _to_date,
    _to_record,
    rows_to_records,
)


def _row(**over):
    base = {
        "Case Number": "09-CC-001607",
        "Defendant  Name": "ROGERS, JAMES",
        "Defendant Street Address": "6504 SALINE STREET TAMPA FL 33634",
        "Plaintiff (Creditor)": "FINANCIAL PORTFOLIOS II INC",
        "Garnishee": "BANK OF AMERICA, N.A.",
        "Writ of Garnishment Filed Date": date(2026, 5, 20),
        "Writ of Garnishment Issued Date": date(2026, 5, 22),
    }
    base.update(over)
    return base


def test_clean_normalizes_whitespace_and_nan():
    assert _clean("2905 W STOVALL ST TAMPA\tFL 33629") == "2905 W STOVALL ST TAMPA FL 33629"
    assert _clean("  a   b  ") == "a b"
    assert _clean("nan") == "" and _clean(None) == ""


def test_to_date_coerces_forms():
    assert _to_date(datetime(2026, 5, 20, 9, 0)) == date(2026, 5, 20)
    assert _to_date(date(2026, 5, 20)) == date(2026, 5, 20)
    assert _to_date("2026-05-20") == date(2026, 5, 20)
    assert _to_date(None) is None and _to_date("nat") is None


def test_to_record_maps_and_derives_exemption():
    rec = _to_record(_row())
    assert rec is not None
    assert rec.case_number == "09-CC-001607"
    assert rec.debtor_name == "ROGERS, JAMES"          # split downstream by name_utils
    assert rec.state == "FL" and rec.county == "Hillsborough"  # not Miami-Dade default
    assert rec.filing_date == date(2026, 5, 20)         # freshness = writ FILED date
    assert rec.exemption_deadline == date(2026, 5, 22) + timedelta(days=_EXEMPTION_WINDOW_DAYS)
    assert rec.garnishment_type == "wage"
    assert rec.source_url.startswith("manual_import:")


def test_to_record_requires_case_name_address():
    assert _to_record(_row(**{"Case Number": ""})) is None
    assert _to_record(_row(**{"Defendant  Name": ""})) is None
    assert _to_record(_row(**{"Defendant Street Address": "  "})) is None


def test_to_record_no_issued_date_leaves_exemption_none():
    rec = _to_record(_row(**{"Writ of Garnishment Issued Date": None}))
    assert rec.exemption_deadline is None


def test_rows_to_records_dedupes_multiple_garnishees_to_one_lead():
    # same case + debtor, two garnishee rows -> one lead
    rows = [
        _row(**{"Garnishee": "BANK OF AMERICA, N.A."}),
        _row(**{"Garnishee": "ETRADE FINANCIAL CORPORATION"}),
        _row(**{"Case Number": "16-CC-006695", "Defendant  Name": "RICHTER, RICKY",
                "Defendant Street Address": "1605 BURNING TREE LN BRANDON FL 33510"}),
    ]
    recs = rows_to_records(rows)
    assert len(recs) == 2
    assert {r.case_number for r in recs} == {"09-CC-001607", "16-CC-006695"}
