from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

BASE = "https://data.searchbug.com/api/search.aspx"

_COMPANY_TERMS = {
    "llc", "inc", "corp", "lp", "llp", "trust", "properties", "apartments",
    "management", "holdings", "group", "realty", "enterprises",
}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_STRIP_CHARS = str.maketrans("", "", ".,'-")


@dataclass(frozen=True)
class SearchBugResult:
    status: str
    phone: str | None = None
    resolved_address: str | None = None
    rows: int = 0
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


def _error_code(message: str | None) -> str | None:
    match = re.search(r"Error Code:\s*([0-9]+)", message or "", re.IGNORECASE)
    return match.group(1) if match else None


def _is_account_error(message: str | None) -> bool:
    value = (message or "").lower()
    return "prepaid plan" in value or "balance is required" in value


def _creds() -> tuple[str, str]:
    co_code = os.environ.get("SEARCHBUG_CO_CODE", "")
    api_key = os.environ.get("SEARCHBUG_API_KEY", "")
    if not co_code or not api_key:
        raise RuntimeError("SEARCHBUG_CO_CODE and SEARCHBUG_API_KEY must be set")
    return co_code, api_key


def _name_matches(expected: str, returned: str | None) -> bool:
    if not returned or not returned.strip():
        return False

    def _norm(name: str) -> list[str]:
        tokens = name.strip().lower().translate(_STRIP_CHARS).split()
        return [t for t in tokens if t not in _NAME_SUFFIXES]

    ret_tokens = _norm(returned)
    if any(t in _COMPANY_TERMS for t in ret_tokens):
        return False

    exp_tokens = _norm(expected)
    if not exp_tokens or not ret_tokens:
        return False

    if set(exp_tokens) == set(ret_tokens):
        return True

    first = exp_tokens[0]
    last = exp_tokens[-1] if len(exp_tokens) > 1 else None
    if last and first in ret_tokens and last in ret_tokens:
        return True

    return False


def _as_list(val) -> list:
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def _as_dict_list(val) -> list[dict]:
    return [item for item in _as_list(val) if isinstance(item, dict)]


def _best_phone(phones_raw) -> str | None:
    phones = _as_dict_list(phones_raw)
    if not phones:
        return None
    mobile = [p for p in phones if (p.get("phoneType") or "").lower() == "mobile"]
    pool = mobile if mobile else phones
    return pool[0].get("phoneNumber") or None


def _parse_last_date(addr: dict) -> datetime:
    raw = addr.get("lastDate") or ""
    try:
        return datetime.strptime(raw, "%m/%d/%Y")
    except Exception:
        return datetime.min


def _most_recent_address(addresses_raw) -> dict | None:
    addrs = _as_dict_list(addresses_raw)
    if not addrs:
        return None
    return max(addrs, key=_parse_last_date)


async def search_tenant(
    first_name: str,
    last_name: str,
    city: str,
    state: str,
    postal: str = "",
) -> tuple[str | None, str | None]:
    """Backward-compatible wrapper returning (phone, resolved_address)."""
    result = await search_tenant_detailed(first_name, last_name, city, state, postal)
    return result.phone, result.resolved_address


async def search_tenant_detailed(
    first_name: str,
    last_name: str,
    city: str,
    state: str,
    postal: str = "",
) -> SearchBugResult:
    """SearchBug People Search with structured miss/error reasons."""
    if not first_name or not last_name:
        return SearchBugResult("invalid_name")

    co_code, api_key = _creds()
    payload = {
        "CO_CODE": co_code,
        "PASS": api_key,
        "TYPE": "api_ppl",
        "FNAME": first_name,
        "LNAME": last_name,
        "CITY": city,
        "STATE": state,
        "FORMAT": "JSON",
        "LIMIT": "5",
    }
    if postal:
        payload["ZIP"] = postal

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(BASE, data=payload)

    if r.status_code != 200:
        error = f"HTTP {r.status_code}: {r.text[:200]}"
        log.warning("SearchBug %s for %s %s", error, first_name, last_name)
        return SearchBugResult("http_error", error=error, retryable=True)

    data = r.json()
    if data.get("Status") == "Error":
        error = str(data.get("Error") or "")
        log.warning("SearchBug error for %s %s: %s", first_name, last_name, error)
        status = "account_error" if _is_account_error(error) else "api_error"
        return SearchBugResult(
            status,
            error=error,
            error_code=_error_code(error),
            retryable=True,
        )

    rows = int(data.get("rows") or 0)
    if rows == 0:
        log.info("SearchBug: no results for %s %s / %s %s", first_name, last_name, city, state)
        return SearchBugResult("no_records")

    if rows > 1:
        log.info(
            "SearchBug: ambiguous - %s matches for %s %s / %s %s",
            rows,
            first_name,
            last_name,
            city,
            state,
        )
        return SearchBugResult("ambiguous", rows=rows)

    people = _as_dict_list((data.get("people") or {}).get("person"))
    if not people:
        return SearchBugResult("no_person", rows=rows)

    person = people[0]
    full_expected = f"{first_name} {last_name}"
    names = _as_dict_list((person.get("names") or {}).get("name"))
    primary_name = ""
    if names:
        n = names[0]
        primary_name = f"{n.get('firstName', '')} {n.get('lastName', '')}".strip()

    if not _name_matches(full_expected, primary_name):
        log.info("SearchBug name mismatch: expected=%r, got=%r", full_expected, primary_name)
        return SearchBugResult("name_mismatch", rows=rows)

    phone = _best_phone((person.get("phones") or {}).get("phone"))
    addr = _most_recent_address((person.get("addresses") or {}).get("address"))
    resolved_address = None
    if addr:
        parts = [
            addr.get("fullStreet", ""),
            addr.get("city", ""),
            f"{addr.get('state', '')} {addr.get('zip', '')}".strip(),
        ]
        resolved_address = ", ".join(p for p in parts if p) or None

    log.info(
        "SearchBug matched %s: phone=%s, address=%s (rows=%s)",
        full_expected,
        "yes" if phone else "no",
        "yes" if resolved_address else "no",
        rows,
    )
    if not phone:
        return SearchBugResult("no_phone", resolved_address=resolved_address, rows=rows)
    return SearchBugResult(
        "phone_found",
        phone=phone,
        resolved_address=resolved_address,
        rows=rows,
    )
