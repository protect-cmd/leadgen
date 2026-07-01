from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Force services.ists_store into sys.modules so patch() can find its attributes.
import services.ists_store as _ists_store

from jobs.run_indiana_ists import _metrics, _to_judgment, main
from models.filing import Filing
from models.judgment import JudgmentRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _filing(**kwargs) -> Filing:
    defaults = dict(
        case_number="49K01-2606-EV-001234",
        tenant_name="Smith, John",
        property_address="123 Oak St, Indianapolis, IN 46201",
        landlord_name="Oak Properties LLC",
        filing_date=date(2026, 6, 7),
        state="IN",
        county="Marion",
        notice_type="Eviction Judgment",
        source_url="https://public.courts.in.gov/mycase/#/vw/CaseSummary/tok1",
        judgment_date=date(2026, 6, 24),
    )
    defaults.update(kwargs)
    return Filing(**defaults)


def _run(coro):
    """Run a coroutine in a fresh event loop (safe inside sync pytest)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _to_judgment — field mapping
# ---------------------------------------------------------------------------

def test_to_judgment_returns_judgment_record():
    assert isinstance(_to_judgment(_filing()), JudgmentRecord)

def test_to_judgment_case_number():
    assert _to_judgment(_filing()).case_number == "49K01-2606-EV-001234"

def test_to_judgment_defendant_name_from_tenant():
    assert _to_judgment(_filing()).defendant_name == "Smith, John"

def test_to_judgment_property_address():
    assert _to_judgment(_filing()).property_address == "123 Oak St, Indianapolis, IN 46201"

def test_to_judgment_plaintiff_name_from_landlord():
    assert _to_judgment(_filing()).plaintiff_name == "Oak Properties LLC"

def test_to_judgment_state():
    assert _to_judgment(_filing()).state == "IN"

def test_to_judgment_county():
    assert _to_judgment(_filing()).county == "Marion"

def test_to_judgment_judgment_date():
    assert _to_judgment(_filing()).judgment_date == date(2026, 6, 24)

def test_to_judgment_source_url():
    assert _to_judgment(_filing()).source_url == (
        "https://public.courts.in.gov/mycase/#/vw/CaseSummary/tok1"
    )

def test_to_judgment_judgment_in_favor_of_is_plaintiff():
    assert _to_judgment(_filing()).judgment_in_favor_of == "Plaintiff"

def test_to_judgment_none_judgment_date_preserved():
    assert _to_judgment(_filing(judgment_date=None)).judgment_date is None


# ---------------------------------------------------------------------------
# _metrics
# ---------------------------------------------------------------------------

def test_metrics_empty():
    assert "no tenant-lost judgments found" in _metrics([])

def test_metrics_counts_records():
    r = JudgmentRecord(
        case_number="A", defendant_name="x", property_address="y",
        judgment_date=date(2026, 6, 28),
    )
    assert "records=1" in _metrics([r])

def test_metrics_prior_phone_count():
    records = [
        JudgmentRecord(case_number="A", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 28), prior_phone=True),
        JudgmentRecord(case_number="B", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 28), prior_phone=False),
    ]
    assert "prior_phone=1" in _metrics(records)

def test_metrics_prior_called_count():
    records = [
        JudgmentRecord(case_number="A", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 28), prior_bland_status="called"),
        JudgmentRecord(case_number="B", defendant_name="x", property_address="y",
                       judgment_date=date(2026, 6, 28), prior_bland_status=None),
    ]
    assert "prior_called=1" in _metrics(records)


# ---------------------------------------------------------------------------
# main() helpers
# ---------------------------------------------------------------------------

def _scraper_mock(filings, last_error=None):
    m = MagicMock()
    m.scrape = AsyncMock(return_value=filings)
    m.last_error = last_error
    return m


def _store_patches(existing=None):
    """Return a stack of context managers that mock ists_store functions."""
    return (
        patch("services.ists_store.existing_case_numbers",
              AsyncMock(return_value=existing or set())),
        patch("services.ists_store.upsert_judgment", AsyncMock()),
    )


# ---------------------------------------------------------------------------
# main() — dry run
# ---------------------------------------------------------------------------

def test_main_dry_run_does_not_upsert():
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert, \
         patch("services.ists_store.existing_case_numbers", AsyncMock(return_value=set())):
        _run(main(dry_run=True))
    mock_upsert.assert_not_called()


def test_main_dry_run_does_not_query_existing():
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())) as mock_existing:
        _run(main(dry_run=True))
    mock_existing.assert_not_called()


# ---------------------------------------------------------------------------
# main() — live run
# ---------------------------------------------------------------------------

def test_main_upserts_new_record():
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert:
        _run(main(dry_run=False))
    mock_upsert.assert_called_once()
    assert mock_upsert.call_args[0][0].case_number == "49K01-2606-EV-001234"


def test_main_skips_existing_record():
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value={"49K01-2606-EV-001234"})), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert:
        _run(main(dry_run=False))
    mock_upsert.assert_not_called()


def test_main_upserts_only_new_when_mixed():
    f1 = _filing(case_number="49K01-2606-EV-001234")
    f2 = _filing(case_number="49K01-2606-EV-001235")
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([f1, f2])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value={"49K01-2606-EV-001234"})), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert:
        _run(main(dry_run=False))
    assert mock_upsert.call_count == 1
    assert mock_upsert.call_args[0][0].case_number == "49K01-2606-EV-001235"


def test_main_empty_filings_no_upsert():
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert:
        _run(main(dry_run=False))
    mock_upsert.assert_not_called()


def test_main_logs_scraper_error(caplog):
    import logging
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([], last_error="portal blocked")), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=lambda r: r)), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())), \
         patch("services.ists_store.upsert_judgment", AsyncMock()):
        with caplog.at_level(logging.ERROR, logger="ists.indiana"):
            _run(main(dry_run=False))
    assert "portal blocked" in caplog.text


def test_main_passes_records_through_annotate_prior_work():
    annotate = AsyncMock(side_effect=lambda r: r)
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work", annotate), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())), \
         patch("services.ists_store.upsert_judgment", AsyncMock()):
        _run(main(dry_run=False))
    annotate.assert_called_once()


def test_main_continues_if_annotate_raises(caplog):
    """If annotate_prior_work fails (e.g. fake credentials), run continues."""
    import logging
    with patch("jobs.run_indiana_ists.IndianaISTSScraper",
               return_value=_scraper_mock([_filing()])), \
         patch("jobs.run_indiana_ists.annotate_prior_work",
               new=AsyncMock(side_effect=Exception("connection refused"))), \
         patch("services.ists_store.existing_case_numbers",
               AsyncMock(return_value=set())), \
         patch("services.ists_store.upsert_judgment", AsyncMock()) as mock_upsert:
        with caplog.at_level(logging.WARNING, logger="ists.indiana"):
            _run(main(dry_run=False))
    assert "annotate_prior_work skipped" in caplog.text
    mock_upsert.assert_called_once()
