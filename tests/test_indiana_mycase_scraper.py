from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scrapers.indiana.mycase import (
    IndianaMyCaseScraper,
    _EV_CASE_TYPE,
    _RESULTS_CAP,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DEFENDANT_PARTY = {
    "Name": "WILLIS, JAHILL",
    "Connection": 2,
    "Address": {
        "Line1": "3720 N PENNSYLVANIA ST #40",
        "City": "INDIANAPOLIS",
        "State": "IN",
        "Zip": "46205",
    },
}

_PLAINTIFF_PARTY = {
    "Name": "SMITH PROPERTIES LLC",
    "Connection": 3,
    "Address": {},
}

_HEARING_EVENT = {
    "Description": "Eviction Hearing",
    "HearingEvent": {
        "Sessions": [{"SessionDate": "07/28/2099", "SessionTime": "3:00 PM"}],
    },
}

_CASE_STUB = {
    "CaseNumber": "49K01-2606-EV-002554",
    "CaseToken": "token_abc",
    "CaseType": _EV_CASE_TYPE,
    "FileDate": "06/30/2026",
    "Court": "Marion County",
}

_DETAIL_FULL = {
    "Parties": [_DEFENDANT_PARTY, _PLAINTIFF_PARTY],
    "Events": [_HEARING_EVENT],
}

_DETAIL_NO_ADDRESS = {
    "Parties": [
        {
            "Name": "JONES, TAMARA",
            "Connection": 2,
            "Address": {},
        },
        _PLAINTIFF_PARTY,
    ],
    "Events": [],
}

_SEARCH_ONE_CASE = {
    "TotalResults": 1,
    "Results": [_CASE_STUB],
}

_SEARCH_EMPTY = {
    "TotalResults": 0,
    "Results": [],
}


def _mock_session(*, init_raises=False, search_data=None, detail_data=None):
    """Build a mock requests.Session with controlled responses."""
    session = MagicMock()

    init_resp = MagicMock()
    if init_raises:
        init_resp.raise_for_status.side_effect = Exception("connection refused")
    else:
        init_resp.raise_for_status.return_value = None

    detail_resp = MagicMock()
    detail_resp.raise_for_status.return_value = None
    detail_resp.json.return_value = detail_data or _DETAIL_FULL

    # GET: first call is session init, subsequent calls are CaseSummary detail
    session.get.side_effect = [init_resp] + [detail_resp] * 20

    search_resp = MagicMock()
    search_resp.raise_for_status.return_value = None
    search_resp.json.return_value = search_data or _SEARCH_ONE_CASE

    session.post.return_value = search_resp

    return session


# ---------------------------------------------------------------------------
# _format_address
# ---------------------------------------------------------------------------


def test_format_address_all_fields():
    addr = {
        "Line1": "3720 N PENNSYLVANIA ST #40",
        "City": "INDIANAPOLIS",
        "State": "IN",
        "Zip": "46205",
    }
    result = IndianaMyCaseScraper._format_address(addr)
    assert result == "3720 N PENNSYLVANIA ST #40, INDIANAPOLIS, IN 46205"


def test_format_address_no_zip():
    addr = {"Line1": "100 Main St", "City": "GARY", "State": "IN", "Zip": ""}
    result = IndianaMyCaseScraper._format_address(addr)
    assert result == "100 Main St, GARY, IN"


def test_format_address_no_line1_returns_empty():
    addr = {"Line1": "", "City": "INDIANAPOLIS", "State": "IN", "Zip": "46201"}
    assert IndianaMyCaseScraper._format_address(addr) == ""


def test_format_address_empty_dict_returns_empty():
    assert IndianaMyCaseScraper._format_address({}) == ""


def test_format_address_none_returns_empty():
    assert IndianaMyCaseScraper._format_address(None) == ""


# ---------------------------------------------------------------------------
# _extract_defendant / _extract_plaintiff
# ---------------------------------------------------------------------------


def test_extract_defendant_finds_connection_2():
    parties = [_PLAINTIFF_PARTY, _DEFENDANT_PARTY]
    name, addr = IndianaMyCaseScraper._extract_defendant(parties)
    assert name == "WILLIS, JAHILL"
    assert "3720 N PENNSYLVANIA" in addr


def test_extract_defendant_returns_empty_when_no_defendant():
    name, addr = IndianaMyCaseScraper._extract_defendant([_PLAINTIFF_PARTY])
    assert name == ""
    assert addr == ""


def test_extract_defendant_empty_address_when_address_missing():
    party = {"Name": "DOE, JOHN", "Connection": 2, "Address": {}}
    name, addr = IndianaMyCaseScraper._extract_defendant([party])
    assert name == "DOE, JOHN"
    assert addr == ""


def test_extract_plaintiff_finds_connection_3():
    name = IndianaMyCaseScraper._extract_plaintiff([_DEFENDANT_PARTY, _PLAINTIFF_PARTY])
    assert name == "SMITH PROPERTIES LLC"


def test_extract_plaintiff_returns_empty_when_none():
    name = IndianaMyCaseScraper._extract_plaintiff([_DEFENDANT_PARTY])
    assert name == ""


# ---------------------------------------------------------------------------
# _extract_county
# ---------------------------------------------------------------------------


def test_extract_county_strips_county_suffix():
    assert IndianaMyCaseScraper._extract_county("Marion County") == "Marion"


def test_extract_county_passthrough_when_no_suffix():
    assert IndianaMyCaseScraper._extract_county("Indianapolis") == "Indianapolis"


def test_extract_county_empty_string_passthrough():
    assert IndianaMyCaseScraper._extract_county("") == ""


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


def test_parse_date_mm_slash_dd_slash_yyyy():
    assert IndianaMyCaseScraper._parse_date("06/30/2026") == date(2026, 6, 30)


def test_parse_date_iso_format():
    assert IndianaMyCaseScraper._parse_date("2026-06-30") == date(2026, 6, 30)


def test_parse_date_strips_time_component():
    assert IndianaMyCaseScraper._parse_date("2026-06-30T00:00:00") == date(2026, 6, 30)


def test_parse_date_invalid_raises():
    with pytest.raises(ValueError):
        IndianaMyCaseScraper._parse_date("not-a-date")


# ---------------------------------------------------------------------------
# _first_hearing_date
# ---------------------------------------------------------------------------


def test_first_hearing_date_returns_upcoming():
    events = [_HEARING_EVENT]
    result = IndianaMyCaseScraper._first_hearing_date(events)
    assert result == date(2099, 7, 28)


def test_first_hearing_date_returns_none_when_no_hearing_event():
    events = [{"Description": "Case Filed", "HearingEvent": None}]
    assert IndianaMyCaseScraper._first_hearing_date(events) is None


def test_first_hearing_date_returns_none_for_empty_list():
    assert IndianaMyCaseScraper._first_hearing_date([]) is None


def test_first_hearing_date_skips_past_dates():
    past_event = {
        "HearingEvent": {
            "Sessions": [{"SessionDate": "01/01/2000", "SessionTime": "9:00 AM"}]
        }
    }
    assert IndianaMyCaseScraper._first_hearing_date([past_event]) is None


# ---------------------------------------------------------------------------
# _scrape_sync — EV case type filtering
# ---------------------------------------------------------------------------


def test_scraper_filters_non_ev_case_types(monkeypatch):
    non_ev = {
        "TotalResults": 2,
        "Results": [
            {**_CASE_STUB, "CaseType": "CC - Civil Collection"},
            {**_CASE_STUB, "CaseNumber": "49K01-2606-CC-000001", "CaseType": "CC - Civil Collection"},
        ],
    }
    session = _mock_session(search_data=non_ev)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_scraper_only_keeps_ev_case_type(monkeypatch):
    mixed = {
        "TotalResults": 2,
        "Results": [
            _CASE_STUB,
            {**_CASE_STUB, "CaseNumber": "49K01-2606-CC-000001", "CaseType": "CC - Civil Collection"},
        ],
    }
    session = _mock_session(search_data=mixed)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].case_number == "49K01-2606-EV-002554"


# ---------------------------------------------------------------------------
# _scrape_sync — deduplication
# ---------------------------------------------------------------------------


def test_scraper_deduplicates_same_case_number(monkeypatch):
    dupes = {
        "TotalResults": 2,
        "Results": [_CASE_STUB, _CASE_STUB],  # same case twice
    }
    session = _mock_session(search_data=dupes)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1


# ---------------------------------------------------------------------------
# _scrape_sync — error handling
# ---------------------------------------------------------------------------


def test_scraper_returns_empty_when_session_init_fails(monkeypatch):
    session = _mock_session(init_raises=True)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    filings = scraper._scrape_sync()

    assert filings == []


def test_scraper_returns_empty_when_search_returns_nothing(monkeypatch):
    session = _mock_session(search_data=_SEARCH_EMPTY)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_scraper_skips_case_with_no_defendant(monkeypatch):
    no_defendant = {
        "Parties": [_PLAINTIFF_PARTY],
        "Events": [],
    }
    session = _mock_session(detail_data=no_defendant)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_scraper_skips_case_with_missing_token(monkeypatch):
    no_token = {
        "TotalResults": 1,
        "Results": [{**_CASE_STUB, "CaseToken": ""}],
    }
    session = _mock_session(search_data=no_token)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings == []


def test_scraper_continues_when_detail_fetch_raises(monkeypatch):
    """A failed detail fetch for one case should not abort the rest."""
    two_cases = {
        "TotalResults": 2,
        "Results": [
            _CASE_STUB,
            {**_CASE_STUB, "CaseNumber": "49K01-2606-EV-099999", "CaseToken": "token_xyz"},
        ],
    }

    detail_ok = MagicMock()
    detail_ok.raise_for_status.return_value = None
    detail_ok.json.return_value = _DETAIL_FULL

    detail_fail = MagicMock()
    detail_fail.raise_for_status.side_effect = Exception("timeout")

    init_resp = MagicMock()
    init_resp.raise_for_status.return_value = None

    search_resp = MagicMock()
    search_resp.raise_for_status.return_value = None
    search_resp.json.return_value = two_cases

    session = MagicMock()
    session.get.side_effect = [init_resp, detail_fail, detail_ok]
    session.post.return_value = search_resp

    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1


# ---------------------------------------------------------------------------
# _scrape_sync — output schema
# ---------------------------------------------------------------------------


def test_scraper_sets_unknown_address_when_address_missing(monkeypatch):
    session = _mock_session(detail_data=_DETAIL_NO_ADDRESS)
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    assert filings[0].property_address == "Unknown"


def test_scraper_sets_correct_state_and_notice_type(monkeypatch):
    session = _mock_session()
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    f = filings[0]
    assert f.state == "IN"
    assert f.notice_type == "Eviction"
    assert f.county == "Marion"


def test_scraper_extracts_defendant_and_plaintiff(monkeypatch):
    session = _mock_session()
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert len(filings) == 1
    f = filings[0]
    assert "WILLIS" in f.tenant_name
    assert f.landlord_name == "SMITH PROPERTIES LLC"


def test_scraper_extracts_filing_date(monkeypatch):
    session = _mock_session()
    scraper = IndianaMyCaseScraper(lookback_days=1)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert filings[0].filing_date == date(2026, 6, 30)


# ---------------------------------------------------------------------------
# _search_range — pagination bisection when cap is hit
# ---------------------------------------------------------------------------


def test_scraper_bisects_date_range_when_cap_hit(monkeypatch):
    """
    When TotalResults == _RESULTS_CAP the scraper must bisect the date range
    and merge results from both halves. Verify it calls POST more than once
    and returns deduplicated cases from each half.
    """
    left_case  = {**_CASE_STUB, "CaseNumber": "49K01-2606-EV-000001", "CaseToken": "tok_left"}
    right_case = {**_CASE_STUB, "CaseNumber": "49K01-2606-EV-000002", "CaseToken": "tok_right"}

    cap_response   = {"TotalResults": _RESULTS_CAP, "Results": []}
    left_response  = {"TotalResults": 1, "Results": [left_case]}
    right_response = {"TotalResults": 1, "Results": [right_case]}

    def make_resp(data):
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.json.return_value = data
        return r

    detail_resp = MagicMock()
    detail_resp.raise_for_status.return_value = None
    detail_resp.json.return_value = _DETAIL_FULL

    init_resp = MagicMock()
    init_resp.raise_for_status.return_value = None

    session = MagicMock()
    session.get.side_effect = [init_resp] + [detail_resp] * 10
    session.post.side_effect = [
        make_resp(cap_response),
        make_resp(left_response),
        make_resp(right_response),
    ]

    scraper = IndianaMyCaseScraper(lookback_days=7)
    monkeypatch.setattr(scraper, "_new_session", lambda: session)

    with patch("scrapers.indiana.mycase.time.sleep"):
        filings = scraper._scrape_sync()

    assert session.post.call_count == 3
    case_numbers = {f.case_number for f in filings}
    assert "49K01-2606-EV-000001" in case_numbers
    assert "49K01-2606-EV-000002" in case_numbers
