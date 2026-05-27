"""9-gate enrichment filter. Codifies the select-searchbug-tenant-leads skill
as runtime policy in pipeline/runner.py.

Each gate returns True to pass, False to skip the filing.
"""
from __future__ import annotations
import re
from datetime import date

from services.name_utils import clean_tenant_name, parse_name

_STREET_NUM_RE = re.compile(r"^\s*\d+\s+")
_ADDR_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}\b")
_ENTITY_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES|"
    r"ESTATE\s+OF|DBA|C/O|S\.A\.|BANK)\b",
    re.IGNORECASE,
)
_BAD_TOKEN_RE = re.compile(r"\b(AKA|OCCUPANTS?|ALL\s+OTHER|ET\s+AL)\b", re.IGNORECASE)


def gate_filing_window(filing_date: date, today: date, window_days: int) -> bool:
    return (today - filing_date).days <= window_days


def gate_court_date(court_date: date | None, today: date) -> bool:
    return court_date is None or court_date >= today


def gate_address(address: str) -> bool:
    if not address:
        return False
    if not _STREET_NUM_RE.match(address):
        return False
    if not _ADDR_STATE_ZIP_RE.search(address):
        return False
    return True


def gate_name(tenant_name: str) -> bool:
    cleaned = clean_tenant_name(tenant_name)
    if not cleaned:
        return False
    if _ENTITY_RE.search(cleaned):
        return False
    if _BAD_TOKEN_RE.search(cleaned):
        return False
    first, last = parse_name(cleaned)
    return bool(first and last)


def gate_query_dedup(first: str, last: str, street: str, zip_: str, seen: set[str]) -> bool:
    key = f"{first.lower()}|{last.lower()}|{street.lower()}|{zip_}"
    if key in seen:
        return False
    seen.add(key)
    return True
