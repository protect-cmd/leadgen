from __future__ import annotations

import importlib
from datetime import date

import pytest

from scrapers.florida import sarasota_cosner as s


def test_parse_summons_address(monkeypatch):
    monkeypatch.setattr(
        s,
        "_extract_pdf_text",
        lambda _: (
            "IN THE COUNTY COURT\n"
            "MIDLAND CREDIT MANAGEMENT INC\n"
            "- vs -\n"
            "JANE Q PUBLIC\n"
            "123 MAIN ST APT 4 )\n"
            "SARASOTA, FL 34236\n"
            "DEFENDANT\n"
        ),
    )

    assert s._parse_summons_address(b"pdf") == "123 MAIN ST APT 4, SARASOTA, FL 34236"


def test_parse_claim_amount(monkeypatch):
    monkeypatch.setattr(
        s,
        "_extract_pdf_text",
        lambda _: "Defendant owes Plaintiff the principal balance of $1,696.17.",
    )

    assert s._parse_claim_amount(b"pdf") == (1696.17, "principal")


def test_pdf_parse_helpers_fail_closed_on_invalid_bytes():
    assert s._parse_summons_address(b"not a pdf") is None
    assert s._parse_claim_amount(b"not a pdf") == (None, None)


def test_date_windows_cap_searches_at_28_days():
    windows = s._date_windows(date(2026, 1, 1), date(2026, 3, 1))

    assert windows == [
        (date(2026, 1, 1), date(2026, 1, 28)),
        (date(2026, 1, 29), date(2026, 2, 25)),
        (date(2026, 2, 26), date(2026, 3, 1)),
    ]


@pytest.mark.asyncio
async def test_search_chunks_wide_lookback(monkeypatch):
    scraper = s.SarasotaCosnerScraper()
    calls: list[tuple[date, date]] = []

    async def fake_search_range(page, start, end):
        calls.append((start, end))
        return []

    monkeypatch.setattr(scraper, "_search_range", fake_search_range)

    assert await scraper._search(object(), date(2026, 1, 1), date(2026, 2, 1)) == []
    assert calls == [
        (date(2026, 1, 1), date(2026, 1, 28)),
        (date(2026, 1, 29), date(2026, 2, 1)),
    ]


def test_runner_import_does_not_eagerly_import_cd_store():
    import sys

    sys.modules.pop("jobs.run_cd_sarasota", None)
    sys.modules.pop("services.cd_store", None)

    importlib.import_module("jobs.run_cd_sarasota")

    assert "services.cd_store" not in sys.modules
