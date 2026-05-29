"""Tests for the silence-classifier in diagnose_scraper_silence.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.diagnose_scraper_silence import classify_silence


def test_zero_filings_no_exception_is_no_volume():
    assert classify_silence(filings_count=0, exception=None, pass_rate=0.0) == "no_volume"


def test_connectivity_exception_classified_as_connectivity():
    exc = ConnectionError("timeout reaching portal")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "connectivity"


def test_runtime_error_with_parsing_in_message_is_parsing():
    exc = RuntimeError("Failed to parse calendar PDF: missing case_number")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "parsing"


def test_generic_exception_classified_as_connectivity():
    """Unknown exception type defaults to connectivity (most common cause)."""
    exc = ValueError("unexpected")
    assert classify_silence(filings_count=0, exception=exc, pass_rate=0.0) == "connectivity"


def test_filings_with_zero_pass_rate_is_format_mismatch():
    """Scraper returned filings but all fail gate_address - Maricopa-class issue."""
    assert classify_silence(filings_count=10, exception=None, pass_rate=0.0) == "format_mismatch"


def test_filings_with_low_pass_rate_is_format_mismatch():
    assert classify_silence(filings_count=50, exception=None, pass_rate=0.3) == "format_mismatch"


def test_filings_with_good_pass_rate_is_fixed_now():
    """If filings come through and pass rate is good, the scraper isn't silent."""
    assert classify_silence(filings_count=10, exception=None, pass_rate=0.9) == "fixed_now"
