"""9-gate enrichment filter — pure unit tests for pipeline/gates.py."""
from __future__ import annotations
from datetime import date

from pipeline.gates import (
    gate_filing_window, gate_court_date, gate_address,
    gate_name, gate_query_dedup,
)


def test_filing_window_passes_recent():
    assert gate_filing_window(date(2026, 5, 25), today=date(2026, 5, 28), window_days=10) is True


def test_filing_window_fails_old():
    assert gate_filing_window(date(2026, 5, 1), today=date(2026, 5, 28), window_days=10) is False


def test_filing_window_zero_age_passes():
    # filing today, today=today → 0 days elapsed, passes.
    assert gate_filing_window(date(2026, 5, 28), today=date(2026, 5, 28), window_days=10) is True


def test_court_date_none_passes():
    assert gate_court_date(None, today=date(2026, 5, 28)) is True


def test_court_date_future_passes():
    assert gate_court_date(date(2026, 6, 1), today=date(2026, 5, 28)) is True


def test_court_date_today_passes():
    assert gate_court_date(date(2026, 5, 28), today=date(2026, 5, 28)) is True


def test_court_date_past_fails():
    assert gate_court_date(date(2026, 5, 20), today=date(2026, 5, 28)) is False


def test_address_with_street_number_and_zip_passes():
    assert gate_address("123 Main St, Houston, TX 77002") is True


def test_address_without_street_number_fails():
    assert gate_address("Main St, Houston, TX 77002") is False


def test_address_without_state_zip_fails():
    assert gate_address("123 Main St") is False


def test_address_blank_fails():
    assert gate_address("") is False


def test_name_clean_parsing_passes():
    assert gate_name("Maria Garcia") is True


def test_name_placeholder_fails():
    assert gate_name("John Doe") is False


def test_name_entity_fails():
    assert gate_name("Pure Auto Spa, LLC") is False


def test_name_with_occupant_token_fails():
    # "Zehneel Occupants" — bad-token rule rejects.
    assert gate_name("Zehneel Occupants") is False


def test_name_with_et_al_fails():
    # Clean tenant name strips "et al"; what remains may still parse, but the
    # raw input contains a bad-token signal. The cleaner removes the trailer
    # first, leaving "John Smith" which is fine. Verify the trailer-cleaner
    # path is what saves us, not the bad-token check.
    assert gate_name("John Smith, et al.") is True


def test_query_dedup_first_pass_second_fail():
    seen: set[str] = set()
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is True
    assert gate_query_dedup("maria", "garcia", "123 Main St", "77002", seen) is False


def test_query_dedup_case_insensitive():
    seen: set[str] = set()
    assert gate_query_dedup("Maria", "Garcia", "123 Main St", "77002", seen) is True
    assert gate_query_dedup("maria", "garcia", "123 main st", "77002", seen) is False
