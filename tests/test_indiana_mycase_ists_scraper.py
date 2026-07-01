from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scrapers.indiana.mycase import IndianaMyCaseScraper, _EV_CASE_TYPE
from scrapers.indiana.mycase_ists import IndianaISTSScraper

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DEFENDANT_PARTY = {
    "Name": "SMITH, JOHN",
    "Connection": 2,
    "Address": {
        "Line1": "123 Oak St",
        "City": "INDIANAPOLIS",
        "State": "IN",
        "Zip": "46201",
    },
}

_PLAINTIFF_PARTY = {
    "Name": "OAK PROPERTIES LLC",
    "Connection": 3,
    "Address": {},
}

_CASE_STUB = {
    "CaseNumber": "49K01-2605-EV-001234",
    "CaseToken": "token_ists",
    "CaseType": _EV_CASE_TYPE,
    "FileDate": "05/01/2026",
    "Court": "Marion County",
}

# EventDate verified against real Indiana Odyssey portal data (probe 2026-07-01)
_JUDGMENT_EVENT = {
    "Description": "Judgment Entry",
    "HearingEvent": None,
    "EventDate": "06/01/2026",
}

_DEFAULT_JUDGMENT_EVENT = {
    "Description": "Default Judgment for Plaintiff",
    "HearingEvent": None,
    "EventDate": "06/01/2026",
}

_HEARING_EVENT_ONLY = {
    "Description": "Eviction Hearing",
    "HearingEvent": {
        "Sessions": [{"SessionDate": "07/28/2099", "SessionTime": "3:00 PM"}],
    },
}

_DETAIL_WITH_JUDGMENT = {
    "Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY],
    "Events": [_JUDGMENT_EVENT],
}

_DETAIL_WITHOUT_JUDGMENT = {
    "Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY],
    "Events": [_HEARING_EVENT_ONLY],
}

_SEARCH_ONE_CASE = {
    "TotalResults": 1,
    "Results": [_CASE_STUB],
}


def _mock_session(*, init_raises=False, search_data=None, detail_data=None):
    session = MagicMock()

    init_resp = MagicMock()
    if init_raises:
        init_resp.raise_for_status.side_effect = Exception("connection refused")
    else:
        init_resp.raise_for_status.return_value = None

    detail_resp = MagicMock()
    detail_resp.raise_for_status.return_value = None
    detail_resp.json.return_value = detail_data or _DETAIL_WITH_JUDGMENT

    session.get.side_effect = [init_resp] + [detail_resp] * 20

    search_resp = MagicMock()
    search_resp.raise_for_status.return_value = None
    search_resp.json.return_value = search_data or _SEARCH_ONE_CASE

    session.post.return_value = search_resp

    return session


# ---------------------------------------------------------------------------
# IndianaISTSScraper -- defaults
# ---------------------------------------------------------------------------

def test_ists_scraper_defaults():
    scraper = IndianaISTSScraper()
    assert scraper.lookback_days == 25
    assert scraper.mode == "judgments"
    assert scraper.notice_type == "Eviction Judgment"
    assert scraper.judgment_recency_days == 7


def test_ists_scraper_custom_lookback():
    scraper = IndianaISTSScraper(lookback_days=60)
    assert scraper.lookback_days == 60


def test_ists_scraper_inherits_last_error_attr():
    scraper = IndianaISTSScraper()
    assert scraper.last_error is None


# ---------------------------------------------------------------------------
# _has_judgment
# ---------------------------------------------------------------------------

def test_has_judgment_true_for_judgment_entry():
    events = [{"Description": "Judgment Entry", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_true_for_default_judgment():
    events = [{"Description": "Default Judgment for Plaintiff", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_true_for_writ_for_property():
    # Real Indiana event name observed via probe --inspect-events
    events = [{"Description": "Writ for Property Issued", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_true_for_order_for_writ():
    # Real Indiana event name observed via probe --inspect-events
    events = [{"Description": "Order for Writ", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_true_for_prejudgment_order_for_possession():
    # Real Indiana event name observed via probe --inspect-events
    events = [{"Description": "Prejudgment Order For Possession", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_false_for_hearing_only():
    events = [{"Description": "Eviction Hearing", "HearingEvent": {"Sessions": []}}]
    assert IndianaMyCaseScraper._has_judgment(events) is False


def test_has_judgment_false_for_empty_events():
    assert IndianaMyCaseScraper._has_judgment([]) is False


def test_has_judgment_false_for_case_filed_event():
    events = [{"Description": "Case Filed", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is False


def test_has_judgment_case_insensitive():
    events = [{"Description": "JUDGMENT ENTRY", "HearingEvent": None}]
    assert IndianaMyCaseScraper._has_judgment(events) is True


def test_has_judgment_true_when_mixed_events():
    events = [
        {"Description": "Eviction Hearing", "HearingEvent": {"Sessions": []}},
        {"Description": "Judgment Entry", "HearingEvent": None},
    ]
    assert IndianaMyCaseScraper._has_judgment(events) is True


# ---------------------------------------------------------------------------
# _judgment_date
# ---------------------------------------------------------------------------

def test_judgment_date_extracts_event_date():
    events = [{"Description": "Judgment Entry", "HearingEvent": None, "EventDate": "06/15/2026"}]
    assert IndianaMyCaseScraper._judgment_date(events) == date(2026, 6, 15)


def test_judgment_date_returns_first_match():
    events = [
        {"Description": "Eviction Hearing", "HearingEvent": None, "EventDate": "06/01/2026"},
        {"Description": "Judgment Entry", "HearingEvent": None, "EventDate": "06/15/2026"},
        {"Description": "Order for Writ", "HearingEvent": None, "EventDate": "06/20/2026"},
    ]
    # Returns the FIRST judgment event, not the first non-judgment
    assert IndianaMyCaseScraper._judgment_date(events) == date(2026, 6, 15)


def test_judgment_date_returns_none_for_no_match():
    events = [{"Description": "Eviction Hearing", "HearingEvent": None, "EventDate": "06/01/2026"}]
    assert IndianaMyCaseScraper._judgment_date(events) is None


def test_judgment_date_returns_none_for_empty():
    assert IndianaMyCaseScraper._judgment_date([]) is None


def test_judgment_date_handles_missing_event_date():
    events = [{"Description": "Judgment Entry", "HearingEvent": None}]   # no EventDate key
    assert IndianaMyCaseScraper._judgment_date(events) is None


# ---------------------------------------------------------------------------
# _scrape_sync -- judgment filtering
# Note: judgment_recency_days=99999 bypasses the recency filter so these
# tests focus purely on has-judgment vs no-judgment logic.
# ---------------------------------------------------------------------------

def test_ists_scraper_returns_filing_with_judgment(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_WITH_JUDGMENT)
    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].case_number == "49K01-2605-EV-001234"


def test_ists_scraper_filters_out_cases_without_judgment(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_WITHOUT_JUDGMENT)
    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_ists_scraper_notice_type_is_eviction_judgment(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_WITH_JUDGMENT)
    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].notice_type == "Eviction Judgment"


def test_ists_scraper_state_is_IN(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_WITH_JUDGMENT)
    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings[0].state == "IN"


def test_ists_filing_has_judgment_date_set(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_WITH_JUDGMENT)
    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].judgment_date == date(2026, 6, 1)


def test_ists_scraper_mixed_judgment_and_no_judgment(monkeypatch):
    """Cases with judgment are kept; cases without are filtered out."""
    two_cases = {
        "TotalResults": 2,
        "Results": [
            _CASE_STUB,
            {**_CASE_STUB, "CaseNumber": "49K01-2605-EV-001235", "CaseToken": "token_no_jdg"},
        ],
    }

    detail_with = MagicMock()
    detail_with.raise_for_status.return_value = None
    detail_with.json.return_value = _DETAIL_WITH_JUDGMENT

    detail_without = MagicMock()
    detail_without.raise_for_status.return_value = None
    detail_without.json.return_value = _DETAIL_WITHOUT_JUDGMENT

    init_resp = MagicMock()
    init_resp.raise_for_status.return_value = None

    search_resp = MagicMock()
    search_resp.raise_for_status.return_value = None
    search_resp.json.return_value = two_cases

    session = MagicMock()
    session.get.side_effect = [init_resp, detail_with, detail_without]
    session.post.return_value = search_resp

    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=99999)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].case_number == "49K01-2605-EV-001234"


# ---------------------------------------------------------------------------
# Judgment recency filter
# ---------------------------------------------------------------------------

def test_ists_recency_filter_includes_recent_judgment(monkeypatch):
    """Judgment 3 days ago is included with recency_days=7."""
    fixed_today = date(2026, 7, 1)
    recent_event = {
        "Description": "Judgment Entry",
        "HearingEvent": None,
        "EventDate": "06/28/2026",   # 3 days before fixed_today
    }
    detail = {"Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY], "Events": [recent_event]}
    session = _mock_session(detail_data=detail)

    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=7)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)
    monkeypatch.setattr("scrapers.indiana.mycase_ists.court_today", lambda tz: fixed_today)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1


def test_ists_recency_filter_excludes_old_judgment(monkeypatch):
    """Judgment 10 days ago is excluded with recency_days=7."""
    fixed_today = date(2026, 7, 1)
    old_event = {
        "Description": "Judgment Entry",
        "HearingEvent": None,
        "EventDate": "06/21/2026",   # 10 days before fixed_today
    }
    detail = {"Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY], "Events": [old_event]}
    session = _mock_session(detail_data=detail)

    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=7)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)
    monkeypatch.setattr("scrapers.indiana.mycase_ists.court_today", lambda tz: fixed_today)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_ists_recency_filter_includes_boundary_judgment(monkeypatch):
    """Judgment exactly recency_days ago is included (>= cutoff)."""
    fixed_today = date(2026, 7, 1)
    boundary_event = {
        "Description": "Judgment Entry",
        "HearingEvent": None,
        "EventDate": "06/24/2026",   # exactly 7 days before fixed_today
    }
    detail = {"Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY], "Events": [boundary_event]}
    session = _mock_session(detail_data=detail)

    scraper = IndianaISTSScraper(lookback_days=25, judgment_recency_days=7)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)
    monkeypatch.setattr("scrapers.indiana.mycase_ists.court_today", lambda tz: fixed_today)

    with patch("scrapers.indiana.mycase.time.sleep"), \
         patch("scrapers.indiana.mycase_ists.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
