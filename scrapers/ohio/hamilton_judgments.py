# scrapers/ohio/hamilton_judgments.py
"""ISTS sub-project A3 — Hamilton County OH (Cincinnati) "tenant-lost" judgment sourcing.

The Hamilton County Municipal Court Clerk (courtclerk.org) publishes an eviction
schedule by hearing date (no auth, browser-like headers, no Playwright). The
default case-summary page exposes a `Disposition: MM/DD/YYYY - <DESC>` line — the
judgment outcome AND date — and the party page exposes the defendant street
address. This is the per-case analog to the Harris JP "Judgments Entered" extract
and the Franklin FCMC disposition CSV.

This module reuses the filings scraper (scrapers/ohio/hamilton.py) read-only for
case enumeration + address parsing; it does NOT modify it. The pure parsers
(parse_disposition / parse_parties / judgment_from_case / filter_by_judgment_window)
are browser-free and unit-tested.

Tenant-lost = a single LAST disposition value, verified 2026-06-25/26 against
courtclerk.org case_history_table (JUDGMENT FOR PLAINTIFF -> "ENTRY GRANTING
PLAINTIFF RESTITUTION OF PREMISES" + "WRIT OF RESTITUTION ISSUED"). See
docs/superpowers/specs/2026-06-26-ists-hamilton-judgment-leads-design.md.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from models.judgment import JudgmentRecord
from pipeline.gates import gate_address, gate_name
from scrapers.dates import court_today

# Reuse the filings scraper's enumeration + constants read-only.
from scrapers.ohio.hamilton import (
    BASE_URL,
    CASE_SUMMARY_URL,
    COURT,
    LOCATION,
    REQUEST_DELAY_SECONDS,
    _parse_eviction_schedule,
)

log = logging.getLogger(__name__)

STATE = "OH"
COUNTY = "Hamilton"
COURT_TIMEZONE = "America/New_York"
SOURCE_URL = "https://www.courtclerk.org/records-search/eviction-schedule-search/"

# Tenant-lost disposition set (uppercased). Single clean value — DISMISSED,
# UNDISPOSED, NEW ASSIGNMENT, REFERRED TO MAGISTRATE are all dropped.
TENANT_LOST_DISPOSITIONS = frozenset({"JUDGMENT FOR PLAINTIFF"})

# Judgment-date lookback (mirrors Harris/Franklin W1).
FLOOR_DAYS = 3        # skip judgments newer than this (posting/data lag buffer)
CEILING_DAYS = 30     # skip judgments older than this (W1 legal window)

# Hearing-date scan window. The portal indexes by hearing date, not judgment
# date, and judgments lag hearings by up to ~6 weeks, so scan back far enough to
# fully capture the judgment window (~CEILING + max observed lag).
HEARING_LOOKBACK_DAYS = 75

_DISPO_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s*-\s*(.+)")
_STREET_RE = re.compile(r"^\s*\d")


def _safe_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_disposition(summary_html: str) -> tuple[date | None, str | None, str]:
    """Parse #case_summary_table -> (disposition_date, disposition_desc_UPPER, caption).

    Returns (None, None, caption) when the case is undisposed (no Disposition row)."""
    soup = BeautifulSoup(summary_html, "html.parser")
    table = soup.find("table", {"id": "case_summary_table"})
    if not table:
        return None, None, ""
    rows: dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            rows[cells[0].rstrip(":").strip().upper()] = cells[1]
    caption = rows.get("CASE CAPTION", "")
    m = _DISPO_RE.search(rows.get("DISPOSITION", ""))
    if not m:
        return None, None, caption
    return _safe_date(m.group(1)), m.group(2).strip().upper(), caption


def _address_from_cell(td) -> str:
    """Convert a party Address <td> to 'STREET, CITY, STATE ZIP'.

    Tolerates an alias line (e.g. 'AKA JOHN SMITH') preceding the street line by
    skipping to the first part that begins with a digit."""
    parts = [t.strip() for t in td.stripped_strings if t.strip()]
    start = next((i for i, p in enumerate(parts) if _STREET_RE.match(p)), None)
    if start is None:
        return ""
    street = parts[start]
    csz = parts[start + 1] if start + 1 < len(parts) else ""
    if not csz:
        return street
    tokens = csz.split()
    if len(tokens) >= 3:
        city = " ".join(tokens[:-2])
        state = tokens[-2]
        zip_code = tokens[-1][:5]  # first 5 digits (9-digit ZIPs appear in records)
        return f"{street}, {city}, {state} {zip_code}"
    return f"{street}, {csz}"


def parse_parties(party_html: str) -> dict[str, str]:
    """Parse #party_info_table -> first defendant (tenant) name+address and first
    plaintiff (landlord) name. Party type cell is 'P n' / 'D n'."""
    out = {"tenant": "", "tenant_address": "", "landlord": ""}
    soup = BeautifulSoup(party_html, "html.parser")
    table = soup.find("table", {"id": "party_info_table"})
    if not table:
        return out
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        ptype = tds[2].get_text(strip=True).replace("\xa0", " ").upper()
        if ptype.startswith("D") and not out["tenant"]:
            out["tenant"] = tds[0].get_text(" ", strip=True)
            out["tenant_address"] = _address_from_cell(tds[1])
        elif ptype.startswith("P") and not out["landlord"]:
            out["landlord"] = tds[0].get_text(" ", strip=True)
    return out


def judgment_from_case(
    summary_html: str,
    party_html: str,
    *,
    case_number: str,
    source_url: str = SOURCE_URL,
) -> JudgmentRecord | None:
    """Build a tenant-lost JudgmentRecord from a case's summary + party HTML, or
    None if not tenant-lost / fails the name or address gate. Does NOT apply the
    date window (see filter_by_judgment_window)."""
    dispo_date, dispo_desc, caption = parse_disposition(summary_html)
    if not dispo_desc or dispo_desc not in TENANT_LOST_DISPOSITIONS:
        return None

    parties = parse_parties(party_html)
    tenant = parties["tenant"]
    if not gate_name(tenant):
        return None

    address = parties["tenant_address"]
    if not gate_address(address):
        return None

    plaintiff = parties["landlord"] or (caption.split(" vs.")[0].strip() if caption else "")

    return JudgmentRecord(
        case_number=case_number,
        defendant_name=tenant,  # preserve raw fidelity for SearchBug matching
        property_address=address,
        plaintiff_name=plaintiff or None,
        state=STATE,
        county=COUNTY,
        judgment_date=dispo_date,
        disposition_desc=dispo_desc,
        disposition_date=dispo_date,
        window="W1",
        source_url=source_url,
    )


def filter_by_judgment_window(
    records: list[JudgmentRecord], *, today: date, floor_days: int, ceiling_days: int,
) -> list[JudgmentRecord]:
    """Keep records whose judgment date is in [today-ceiling, today-floor]."""
    out: list[JudgmentRecord] = []
    for r in records:
        d = r.judgment_date
        if d is None:
            continue
        age = (today - d).days
        if floor_days <= age <= ceiling_days:
            out.append(r)
    return out


class HamiltonJudgmentScraper:
    """Enumerates Hamilton eviction cases over a hearing-date lookback, fetches
    each case's disposition, and returns tenant-lost JudgmentRecords whose
    judgment date is in the W1 window. Plain requests — no browser."""

    def __init__(
        self,
        hearing_lookback_days: int = HEARING_LOOKBACK_DAYS,
        floor_days: int = FLOOR_DAYS,
        ceiling_days: int = CEILING_DAYS,
        request_delay: float = REQUEST_DELAY_SECONDS,
    ):
        self.hearing_lookback_days = hearing_lookback_days
        self.floor_days = floor_days
        self.ceiling_days = ceiling_days
        self.request_delay = request_delay
        self.last_error: str | None = None
        self.scanned = 0  # cases whose disposition we fetched (cost metric)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": SOURCE_URL,
        })

    def scrape(self, skip_cases: set[str] | None = None) -> list[JudgmentRecord]:
        """Return tenant-lost JudgmentRecords in the W1 window.

        skip_cases: case numbers already stored (terminal JFP) — skip the
        disposition fetch for these to cut repeat cost on daily runs."""
        self.last_error = None
        self.scanned = 0
        skip = skip_cases or set()
        today = court_today(COURT_TIMEZONE)

        # 1) Enumerate unique case numbers across the hearing-date lookback.
        case_numbers = self._enumerate_cases(today)
        if self.last_error and not case_numbers:
            return []

        # 2) Fetch each case's disposition; build tenant-lost records.
        records: list[JudgmentRecord] = []
        for case_number in case_numbers:
            if case_number in skip:
                continue
            self._throttle()
            summary_html = self._get(f"{CASE_SUMMARY_URL}?casenumber={case_number}&court[{COURT}]=on")
            if summary_html is None:
                continue
            self.scanned += 1
            dispo_date, dispo_desc, _ = parse_disposition(summary_html)
            if not dispo_desc or dispo_desc not in TENANT_LOST_DISPOSITIONS:
                continue
            if dispo_date is None:
                continue
            age = (today - dispo_date).days
            if not (self.floor_days <= age <= self.ceiling_days):
                continue
            # Only now (a confirmed in-window tenant-lost) do we pay for the party page.
            self._throttle()
            party_html = self._post_party(case_number)
            if party_html is None:
                continue
            record = judgment_from_case(
                summary_html, party_html, case_number=case_number,
            )
            if record is not None:
                records.append(record)

        log.info(
            "Hamilton ISTS: %d tenant-lost judgments in W1 window (scanned %d cases of %d enumerated)",
            len(records), self.scanned, len(case_numbers),
        )
        return records

    # ------------------------------------------------------------------ #
    #  HTTP                                                                #
    # ------------------------------------------------------------------ #

    def _enumerate_cases(self, today: date) -> list[str]:
        """Walk the hearing-date lookback and collect unique eviction case numbers."""
        seen: set[str] = set()
        ordered: list[str] = []
        any_ok = False
        for offset in range(self.floor_days, self.hearing_lookback_days + 1):
            target = today - timedelta(days=offset)
            date_str = f"{target.month}/{target.day}/{target.year}"
            url = f"{BASE_URL}?chosendate={date_str}&court={COURT}&location={LOCATION}"
            html = self._get(url)
            if html is None:
                continue
            any_ok = True
            for filing in _parse_eviction_schedule(html, hearing_date=target, source_url=url):
                if filing.case_number and filing.case_number not in seen:
                    seen.add(filing.case_number)
                    ordered.append(filing.case_number)
            self._throttle()
        if not any_ok:
            self.last_error = "Hamilton ISTS: failed to fetch any eviction schedule day"
            log.error(self.last_error)
        return ordered

    def _get(self, url: str) -> str | None:
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            log.warning("Hamilton ISTS: GET failed for %s: %s", url, e)
            return None

    def _post_party(self, case_number: str) -> str | None:
        try:
            r = self.session.post(
                CASE_SUMMARY_URL,
                data={"sec": "party", "casenumber": case_number, "submit": ""},
                headers={"Referer": f"{CASE_SUMMARY_URL}?casenumber={case_number}"},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            return r.text
        except Exception as e:
            log.warning("Hamilton ISTS: party POST failed for %s: %s", case_number, e)
            return None

    def _throttle(self) -> None:
        if self.request_delay > 0:
            time.sleep(self.request_delay)
