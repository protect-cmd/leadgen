from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import httpx
from supabase import create_client, Client
from models.filing import Filing
from models.contact import EnrichedContact, RoutingOutcome
from pipeline.qualification import QualificationOutcome

load_dotenv()

log = logging.getLogger(__name__)
_SUPABASE_RETRY_ATTEMPTS = 3
_SUPABASE_RETRY_DELAY_SECONDS = 1.0

_client: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)


def _execute_with_retry(query, label: str):
    for attempt in range(1, _SUPABASE_RETRY_ATTEMPTS + 1):
        try:
            return query.execute()
        except httpx.TransportError as exc:
            if attempt == _SUPABASE_RETRY_ATTEMPTS:
                raise
            log.warning(
                "Supabase %s transport error on attempt %s/%s: %s",
                label,
                attempt,
                _SUPABASE_RETRY_ATTEMPTS,
                exc,
            )
            time.sleep(_SUPABASE_RETRY_DELAY_SECONDS * attempt)


def _execute_optional_lead_contact_write(query) -> None:
    try:
        _execute_with_retry(query, "lead_contacts optional write")
    except Exception as exc:
        # Keep legacy filing writes alive while the lead_contacts migration is
        # being applied across environments.
        log.warning("lead_contacts write suppressed: %s", exc)
        return


async def has_ng_phone(case_number: str) -> bool:
    """True if a tenant-side (track='ng') phone already exists in lead_contacts."""
    def _query() -> bool:
        result = _execute_with_retry(
            _client.table("lead_contacts")
            .select("case_number")
            .eq("case_number", case_number)
            .eq("track", "ng")
            .not_.is_("phone", "null"),
            "ng phone existence",
        )
        return len(result.data) > 0
    return await asyncio.to_thread(_query)


async def is_duplicate(case_number: str) -> bool:
    def _query() -> bool:
        result = _execute_with_retry(
            _client.table("filings").select("case_number").eq("case_number", case_number),
            "duplicate check",
        )
        return len(result.data) > 0
    return await asyncio.to_thread(_query)


async def insert_filing(filing: Filing) -> None:
    def _insert() -> None:
        _execute_with_retry(
            _client.table("filings").insert({
                "case_number": filing.case_number,
                "tenant_name": filing.tenant_name,
                "property_address": filing.property_address,
                "landlord_name": filing.landlord_name,
                "filing_date": filing.filing_date.isoformat(),
                "court_date": filing.court_date.isoformat() if filing.court_date else None,
                "state": filing.state,
                "county": filing.county,
                "notice_type": filing.notice_type,
                "source_url": filing.source_url,
            }),
            "insert filing",
        )
    await asyncio.to_thread(_insert)


async def backfill_address(case_number: str, address: str) -> bool:
    """Fill in a filing's property_address after the fact.

    Some sources (e.g. Dayton/Montgomery OH) publish the case before the clerk
    populates the property address, so the first scrape stores "Unknown" and
    dedup then skips re-scrapes. This updates the row ONLY when the stored
    address is still "Unknown" and a real address is now available — a no-op for
    sources that already populate the address on first capture.

    Returns True if a row was updated.
    """
    if not address or address == "Unknown":
        return False

    def _update() -> bool:
        result = _execute_with_retry(
            _client.table("filings")
            .update({"property_address": address})
            .eq("case_number", case_number)
            .eq("property_address", "Unknown"),
            "backfill address",
        )
        return bool(result.data)

    return await asyncio.to_thread(_update)


def _enrichment_payload(contact: EnrichedContact) -> dict:
    return {
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "language_hint": contact.language_hint,
    }


_LEAD_CONTACT_COLUMNS_CACHE: set[str] | None = None


def _lead_contact_known_columns() -> set[str]:
    """Discover existing columns on lead_contacts (cached per process).

    Mirrors run_metrics column discovery: when a new field is added to the
    payload but the migration hasn't landed yet, drop the unknown key
    instead of failing the entire write. Migration 013 adds searchbug_*.
    """
    global _LEAD_CONTACT_COLUMNS_CACHE
    if _LEAD_CONTACT_COLUMNS_CACHE is not None:
        return _LEAD_CONTACT_COLUMNS_CACHE
    try:
        sample = _execute_with_retry(
            _client.table("lead_contacts").select("*").limit(1),
            "discover lead_contacts columns",
        )
        if sample.data:
            _LEAD_CONTACT_COLUMNS_CACHE = set(sample.data[0].keys())
        else:
            _LEAD_CONTACT_COLUMNS_CACHE = set()
    except Exception as exc:
        log.warning("Could not discover lead_contacts columns: %s", exc)
        _LEAD_CONTACT_COLUMNS_CACHE = set()
    return _LEAD_CONTACT_COLUMNS_CACHE


def _lead_contact_payload(contact: EnrichedContact) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "case_number": contact.filing.case_number,
        "track": contact.track,
        "contact_name": contact.contact_name,
        "phone": contact.phone,
        "email": contact.email,
        "secondary_address": contact.secondary_address,
        "estimated_rent": contact.estimated_rent,
        "property_type": contact.property_type,
        "language_hint": contact.language_hint,
        "searchbug_status": contact.searchbug_status,
        "searchbug_returned_name": contact.searchbug_returned_name,
        "enrichment_source": "batchdata",
        "updated_at": now,
    }
    # Drop fields the schema doesn't have yet (e.g. before migration 013
    # is applied). Empty set means we couldn't introspect — send as-is and
    # let the API surface a clear error.
    known = _lead_contact_known_columns()
    if known:
        payload = {k: v for k, v in payload.items() if k in known}
    return payload


async def upsert_contact_enrichment(contact: EnrichedContact) -> None:
    def _update() -> None:
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").upsert(
                _lead_contact_payload(contact),
                on_conflict="case_number,track",
            )
        )
        # EC track mirrors enrichment fields onto the legacy filings columns.
        # NG track now persists only via lead_contacts.
        if contact.track == "ec":
            _execute_with_retry(
                _client.table("filings").update(_enrichment_payload(contact)).eq(
                    "case_number",
                    contact.filing.case_number,
                ),
                "update enrichment",
            )
        else:
            _execute_with_retry(
                _client.table("filings").update(
                    {"language_hint": contact.language_hint}
                ).eq("case_number", contact.filing.case_number),
                "update enrichment",
            )
    await asyncio.to_thread(_update)


_UNSAFE_CHARS_RE = re.compile(r"[%,()\"\\]")
_MAX_NOTE_CHARS = 2000


def _sanitize_search_query(q: str | None) -> str:
    """Strip PostgREST filter-breaking chars from user-supplied search input.

    Keeps letters/digits/spaces/hyphens/apostrophes/periods/at-signs.
    Removes %, comma, parens, quotes, and backslashes (used in PostgREST
    filter syntax). Returns empty string on None or whitespace-only input.
    """
    if not q:
        return ""
    return _UNSAFE_CHARS_RE.sub("", q).strip()


def _ists_search_row(r: dict) -> dict:
    """Map an ists_judgments row into the common search-result shape so ISTS
    leads render in the same UI (track='ists', defendant -> tenant_name)."""
    return {
        "case_number": r.get("case_number"),
        "track": "ists",
        "tenant_name": r.get("defendant_name"),
        "contact_name": r.get("defendant_name"),
        "property_address": r.get("property_address"),
        "phone": r.get("phone"),
        "dnc_status": r.get("dnc_status"),
        "ghl_contact_id": r.get("ghl_contact_id"),
        "bland_status": "triggered" if r.get("bland_call_id") else None,
        "landlord_name": r.get("plaintiff_name"),
        "estimated_rent": r.get("estimated_rent"),
        "filing_date": r.get("judgment_date"),   # drives result sort (shared key)
        "judgment_date": r.get("judgment_date"), # shown explicitly in the UI for ISTS
        "court_date": None,
        "state": r.get("state"),
        "county": r.get("county"),
        "notice_type": "Judgment",
        "language_hint": r.get("language_hint"),
    }


def _merge_search_rows(
    contact_rows: list[dict], filing_rows: list[dict],
    ists_rows: list[dict], limit: int
) -> list[dict]:
    """Flatten contact + filing + ISTS-judgment matches into one sorted list,
    deduped by case_number. Vantage rows win a case_number collision; ISTS rows
    fill in the judgment-only leads (no lead_contacts/filings row)."""
    by_case: dict[str, dict] = {}

    # Lead-contact-side rows: contact fields top-level, filing fields nested
    for r in contact_rows:
        f = r.get("filings") or {}
        if isinstance(f, list):
            f = f[0] if f else {}
        merged = {**f, **{k: v for k, v in r.items() if k != "filings"}}
        case_no = merged.get("case_number")
        if case_no:
            by_case[case_no] = merged

    # Filing-side rows: filing fields top-level, contact fields nested
    for r in filing_rows:
        lcs = r.get("lead_contacts") or []
        if isinstance(lcs, dict):
            lcs = [lcs]
        chosen = next(
            (c for c in lcs if c.get("track") == "ng"),
            lcs[0] if lcs else {},
        )
        merged = {**r, **{k: v for k, v in chosen.items() if k != "case_number"}}
        merged.pop("lead_contacts", None)
        case_no = merged.get("case_number")
        if case_no and case_no not in by_case:
            by_case[case_no] = merged

    # ISTS judgments — judgment-only leads (not in lead_contacts/filings)
    for r in ists_rows:
        case_no = r.get("case_number")
        if case_no and case_no not in by_case:
            by_case[case_no] = _ists_search_row(r)

    out = list(by_case.values())
    out.sort(key=lambda r: r.get("filing_date") or "", reverse=True)
    return out[:limit]


async def search_leads(q: str, limit: int = 20) -> list[dict]:
    """Unified search across lead_contacts + filings + ists_judgments.

    Matches substring on name (contact_name + tenant_name + defendant_name),
    phone (digits of q), case_number, and property_address. Merges results by
    case_number, sorts by filing_date DESC, returns up to `limit` rows. ISTS
    judgment leads surface with track='ists' so callbacks can be looked up.

    Returns [] for queries under 2 characters.
    """
    safe_q = _sanitize_search_query(q)
    if len(safe_q) < 2:
        return []

    digits_q = "".join(c for c in safe_q if c.isdigit())

    def _query() -> list[dict]:
        contact_filters = [
            f"contact_name.ilike.%{safe_q}%",
            f"case_number.ilike.%{safe_q}%",
        ]
        if digits_q:
            contact_filters.append(f"phone.ilike.%{digits_q}%")
        contact_or = ",".join(contact_filters)

        contact_rows = (
            _client.table("lead_contacts")
            .select(
                "case_number,track,contact_name,phone,email,property_type,"
                "estimated_rent,secondary_address,language_hint,dnc_status,"
                "searchbug_status,last_called_at,ghl_contact_id,bland_status,"
                "filings(filing_date,court_date,tenant_name,property_address,"
                "landlord_name,state,county,notice_type,source_url,lead_bucket)"
            )
            .or_(contact_or)
            .limit(limit)
            .execute()
            .data
            or []
        )

        filing_or = ",".join([
            f"tenant_name.ilike.%{safe_q}%",
            f"property_address.ilike.%{safe_q}%",
            f"case_number.ilike.%{safe_q}%",
        ])
        filing_rows = (
            _client.table("filings")
            .select(
                "case_number,tenant_name,property_address,landlord_name,"
                "filing_date,court_date,state,county,notice_type,source_url,"
                "lead_bucket,"
                "lead_contacts(track,contact_name,phone,email,property_type,"
                "estimated_rent,secondary_address,searchbug_status,dnc_status,"
                "last_called_at,ghl_contact_id,bland_status)"
            )
            .or_(filing_or)
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

        ists_filters = [
            f"defendant_name.ilike.%{safe_q}%",
            f"case_number.ilike.%{safe_q}%",
            f"property_address.ilike.%{safe_q}%",
        ]
        if digits_q:
            ists_filters.append(f"phone.ilike.%{digits_q}%")
        ists_rows = (
            _client.table("ists_judgments")
            .select(
                "case_number,defendant_name,property_address,phone,dnc_status,"
                "bland_call_id,ghl_contact_id,judgment_date,plaintiff_name,"
                "estimated_rent,state,county,language_hint"
            )
            .or_(",".join(ists_filters))
            .limit(limit)
            .execute()
            .data
            or []
        )

        return _merge_search_rows(contact_rows, filing_rows, ists_rows, limit)

    return await asyncio.to_thread(_query)


async def mark_lead_called(*, case_number: str, track: str) -> str:
    """UPDATE lead_contacts SET last_called_at = now() and return the
    resulting timestamp string. Used by the dashboard 'Mark Called' button."""
    now_iso = datetime.now(timezone.utc).isoformat()

    def _update() -> str:
        _execute_with_retry(
            _client.table("lead_contacts")
            .update({"last_called_at": now_iso})
            .eq("case_number", case_number)
            .eq("track", track),
            "mark lead called",
        )
        return now_iso

    return await asyncio.to_thread(_update)


async def add_lead_note(*, case_number: str, track: str, text: str,
                        author: str = "caller") -> dict:
    """Append a note for (case_number, track). Returns the inserted row.

    Raises ValueError if text is empty/whitespace or exceeds 2000 chars.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("note text is empty")
    if len(stripped) > _MAX_NOTE_CHARS:
        raise ValueError(f"note text exceeds {_MAX_NOTE_CHARS} chars")

    payload = {
        "case_number": case_number,
        "track": track,
        "note_text": stripped,
        "author": author,
    }

    def _insert() -> dict:
        r = _execute_with_retry(
            _client.table("lead_notes").insert(payload),
            "insert lead_note",
        )
        rows = r.data or []
        if not rows:
            raise RuntimeError("INSERT lead_note returned no row")
        return rows[0]

    return await asyncio.to_thread(_insert)


async def list_lead_notes(*, case_number: str, track: str,
                          limit: int = 50) -> list[dict]:
    """Return notes for (case_number, track) sorted by created_at DESC."""
    def _query() -> list[dict]:
        return (
            _client.table("lead_notes")
            .select("id,note_text,author,created_at")
            .eq("case_number", case_number)
            .eq("track", track)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    return await asyncio.to_thread(_query)


async def update_enrichment(contact: EnrichedContact) -> None:
    await upsert_contact_enrichment(contact)


async def update_classification(case_number: str, outcome: QualificationOutcome) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "property_zip": outcome.property_zip,
                "lead_bucket": outcome.lead_bucket,
                "discard_reason": outcome.discard_reason,
                "qualification_notes": outcome.qualification_notes,
                "classified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("case_number", case_number),
            "update classification",
        )
    await asyncio.to_thread(_update)


async def update_language_hint(case_number: str, language_hint: str | None) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "language_hint": language_hint,
            }).eq("case_number", case_number),
            "update language hint",
        )
    await asyncio.to_thread(_update)


async def update_estimated_rent(case_number: str, rent: float) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "estimated_rent": rent,
            }).eq("case_number", case_number),
            "update estimated rent",
        )

    await asyncio.to_thread(_update)


async def update_routing(case_number: str, outcome: RoutingOutcome) -> None:
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                "routed": True,
                "routing_outcome": outcome.action,
            }).eq("case_number", case_number),
            "update routing",
        )
    await asyncio.to_thread(_update)


async def update_contact_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
    column = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    def _update() -> None:
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").update({
                "ghl_contact_id": ghl_contact_id,
            }).eq("case_number", case_number).eq("track", track)
        )
        _execute_with_retry(
            _client.table("filings").update({
                column: ghl_contact_id,
            }).eq("case_number", case_number),
            "update ghl id",
        )
    await asyncio.to_thread(_update)


async def update_ghl_id(case_number: str, ghl_contact_id: str, track: str = "ec") -> None:
    await update_contact_ghl_id(case_number, ghl_contact_id, track)


async def get_lead_row(case_number: str, track: str = "ec") -> dict | None:
    """Return merged filing + contact data for a single case_number/track.

    Used by the dashboard clear_dnc path to reconstruct an EnrichedContact
    before pushing a previously-blocked contact to GHL and Instantly.
    Returns None if no filing exists for the case_number.
    """
    def _query() -> dict | None:
        filing_result = _execute_with_retry(
            _client.table("filings")
            .select(
                "case_number,tenant_name,landlord_name,property_address,"
                "state,county,filing_date,court_date,notice_type,source_url,lead_bucket"
            )
            .eq("case_number", case_number)
            .limit(1),
            "get lead row filing",
        )
        if not filing_result.data:
            return None
        row = dict(filing_result.data[0])
        contact_result = _execute_with_retry(
            _client.table("lead_contacts")
            .select(
                "phone,email,property_type,estimated_rent,"
                "language_hint,ghl_contact_id"
            )
            .eq("case_number", case_number)
            .eq("track", track)
            .limit(1),
            "get lead row contact",
        )
        if contact_result.data:
            row.update(contact_result.data[0])
        return row

    return await asyncio.to_thread(_query)


async def mark_bland_triggered(case_number: str, track: str = "ec") -> None:
    column = "bland_triggered" if track == "ec" else "ng_bland_triggered"
    def _update() -> None:
        _execute_with_retry(
            _client.table("filings").update({
                column: True,
            }).eq("case_number", case_number),
            "mark bland triggered",
        )
    await asyncio.to_thread(_update)


_run_metrics_columns_cache: set[str] | None = None


def _run_metrics_known_columns() -> set[str]:
    """Discover existing columns on run_metrics (cached per process).

    Used by write_run_metrics to drop fields that haven't been migrated yet,
    so a single new metric doesn't blow up the entire run summary write.
    """
    global _run_metrics_columns_cache
    if _run_metrics_columns_cache is not None:
        return _run_metrics_columns_cache
    try:
        sample = _execute_with_retry(
            _client.table("run_metrics").select("*").limit(1),
            "discover run_metrics columns",
        )
        if sample.data:
            _run_metrics_columns_cache = set(sample.data[0].keys())
        else:
            # Empty table — fall back to a known-safe baseline so we at least
            # write something. The known columns will be re-derived next time
            # there's data, or after migration.
            _run_metrics_columns_cache = {
                "run_at", "state", "county", "filings_received",
                "duplicates_skipped", "address_skipped", "batchdata_calls",
                "phones_found", "ghl_created", "bland_triggered",
                "instantly_enrolled", "elapsed_seconds",
            }
    except Exception as exc:
        log.warning("Could not discover run_metrics columns: %s", exc)
        _run_metrics_columns_cache = set()
    return _run_metrics_columns_cache


def _reset_run_metrics_columns_cache_for_tests() -> None:
    """Test-only helper to clear the column cache between tests."""
    global _run_metrics_columns_cache
    _run_metrics_columns_cache = None


async def write_run_metrics(metrics: dict) -> None:
    """Insert a run summary into run_metrics, dropping fields whose columns
    don't exist yet. This makes schema drift non-fatal: new metric fields
    can land in code before the migration runs, and the write still saves
    whatever DOES match the schema instead of erroring out entirely.
    """
    known = _run_metrics_known_columns()

    def _insert() -> None:
        if known:
            filtered = {k: v for k, v in metrics.items() if k in known}
            dropped = sorted(set(metrics) - set(filtered))
            if dropped:
                log.warning(
                    "write_run_metrics: dropped unknown columns %s — "
                    "run schema migration to capture them",
                    dropped,
                )
            payload = filtered
        else:
            # Couldn't introspect the schema; try the original payload and
            # let Supabase reject if a column is missing.
            payload = metrics
        _execute_with_retry(
            _client.table("run_metrics").insert(payload),
            "write run metrics",
        )

    await asyncio.to_thread(_insert)


async def set_bland_status(case_number: str, track: str, status: str, call_id: str | None = None) -> None:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_call_id = "bland_call_id" if track == "ec" else "ng_bland_call_id"
    def _update() -> None:
        payload: dict = {col_status: status}
        lead_payload: dict = {"bland_status": status}
        if call_id:
            payload[col_call_id] = call_id
            lead_payload["bland_call_id"] = call_id
            # Per-fire timestamp for the ops dashboard's fired/day trend. Guard on
            # column discovery: _execute_optional_lead_contact_write suppresses the
            # WHOLE write on error, so sending an unknown column would silently drop
            # bland_call_id too (breaking fire idempotency) until migration 021 lands.
            if "bland_triggered_at" in _lead_contact_known_columns():
                lead_payload["bland_triggered_at"] = datetime.now(timezone.utc).isoformat()
        _execute_optional_lead_contact_write(
            _client.table("lead_contacts").update(lead_payload).eq(
                "case_number",
                case_number,
            ).eq("track", track)
        )
        _execute_with_retry(
            _client.table("filings").update(payload).eq("case_number", case_number),
            "set bland status",
        )
    await asyncio.to_thread(_update)


_PENDING_FILING_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,phone,email,"
    "property_type"
)


def _overlay_contact_rows(
    filing_rows: list[dict],
    contact_rows: list[dict],
    clear_missing_contact: bool = False,
) -> list[dict]:
    contacts_by_case = {row["case_number"]: row for row in contact_rows}
    merged: list[dict] = []
    for filing_row in filing_rows:
        row = dict(filing_row)
        contact_row = contacts_by_case.get(row["case_number"])
        if contact_row:
            row["phone"] = contact_row.get("phone")
            row["email"] = contact_row.get("email")
            row["property_type"] = contact_row.get("property_type") or row.get("property_type")
            row["estimated_rent"] = contact_row.get("estimated_rent") or row.get("estimated_rent")
            row["language_hint"] = contact_row.get("language_hint") or row.get("language_hint")
            row["bland_status"] = contact_row.get("bland_status")
            row["ghl_contact_id"] = contact_row.get("ghl_contact_id")
        elif clear_missing_contact:
            row["phone"] = None
            row["email"] = None
            row["bland_status"] = None
            row["ghl_contact_id"] = None
        merged.append(row)
    return merged


def _get_pending_leads_from_contacts(track: str, limit: int) -> list[dict]:
    contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", track)
        .eq("bland_status", "pending")
        .not_.is_("ghl_contact_id", "null")
        .limit(limit)
        .execute()
        .data
    )
    case_numbers = [row["case_number"] for row in contacts]
    if not case_numbers:
        return []

    filings = (
        _client.table("filings")
        .select(_PENDING_FILING_SELECT)
        .in_("case_number", case_numbers)
        .order("filing_date", desc=True)
        .execute()
        .data
    )
    return _overlay_contact_rows(filings, contacts)


def _get_pending_leads_legacy(track: str, limit: int) -> list[dict]:
    col_status = "bland_status" if track == "ec" else "ng_bland_status"
    col_ghl = "ghl_contact_id" if track == "ec" else "ng_ghl_contact_id"
    return (
        _client.table("filings")
        .select(f"{_PENDING_FILING_SELECT},{col_status},{col_ghl}")
        .eq(col_status, "pending")
        .not_.is_(col_ghl, "null")
        .order("filing_date", desc=True)
        .limit(limit)
        .execute()
        .data
    )


async def get_pending_leads(track: str = "ec", limit: int = 200) -> list[dict]:
    def _query() -> list[dict]:
        try:
            rows = _get_pending_leads_from_contacts(track, limit)
            if rows or track != "ec":
                return rows
            return _get_pending_leads_legacy(track, limit)
        except Exception:
            if track != "ec":
                return []
            return _get_pending_leads_legacy(track, limit)

    return await asyncio.to_thread(_query)


_DASHBOARD_SELECT = (
    "case_number,tenant_name,landlord_name,property_address,"
    "state,county,filing_date,court_date,scraped_at,phone,email,"
    "property_type,estimated_rent,property_zip,lead_bucket,"
    "discard_reason,qualification_notes,language_hint,"
    "bland_status,ghl_contact_id"
)


def _filter_dashboard_query(query, view: str):
    if view in ("ec_residential", "ng_residential"):
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view in ("ec_commercial", "ng_commercial"):
        return query.eq("lead_bucket", "commercial").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    if view == "ng_spanish_residential":
        return query.eq("lead_bucket", "residential_approved").eq("language_hint", "spanish_likely")
    if view == "ng_spanish_commercial":
        return query.eq("lead_bucket", "commercial").eq("language_hint", "spanish_likely")
    if view in ("ec_held", "ng_held"):
        return query.eq("lead_bucket", "held")
    if view in ("ec_discarded", "ng_discarded"):
        return query.eq("lead_bucket", "discarded")
    if view in ("ec_captured", "ng_captured", "captured"):
        return query.eq("lead_bucket", "captured")
    if view == "ng_already_called":
        return query.eq("lead_bucket", "residential_approved").or_(
            "language_hint.is.null,language_hint.neq.spanish_likely"
        )
    # Legacy fallback — residential approved, non-Spanish
    return query.eq("lead_bucket", "residential_approved").or_(
        "language_hint.is.null,language_hint.neq.spanish_likely"
    )


def _track_for_dashboard_view(view: str) -> str:
    return "ng" if view.startswith("ng_") else "ec"


# Bland statuses that indicate a tenant lead has already been worked. Such
# leads are hidden from the main "Vantage Residential" view and surface in
# the "Vantage Already Called" view instead.
NG_WORKED_BLAND_STATUSES: frozenset[str] = frozenset({
    "triggered",            # Bland successfully dialed
    "wrong_brand_review",   # post-push QA flagged
    "missing_contact_data", # enrichment returned nothing dialable
})

# Subset of worked statuses that surface in the "Already Called" tab.
# missing_contact_data is excluded — those weren't called.
NG_ALREADY_CALLED_BLAND_STATUSES: frozenset[str] = frozenset({
    "triggered",
    "wrong_brand_review",
})


def _is_ng_contact_actionable(contact: dict) -> bool:
    """A tenant contact is actionable when it has a phone the operator can
    dial AND it hasn't already been worked.
    """
    if not contact.get("phone"):
        return False
    return contact.get("bland_status") not in NG_WORKED_BLAND_STATUSES


def _is_ng_contact_already_called(contact: dict) -> bool:
    """A tenant contact is 'already called' when Bland completed a dial or
    post-push QA flagged it. Compliance holds (blocked_dnc) and never-dialed
    rows (missing_contact_data) are excluded — they belong elsewhere.
    """
    return contact.get("bland_status") in NG_ALREADY_CALLED_BLAND_STATUSES


def _target_metadata(track: str, view: str) -> dict:
    is_spanish = view in {"ng_spanish_residential", "ng_spanish_commercial"}
    if track == "ng":
        return {
            "target_track": "ng",
            "target_brand": "Vantage Defense Group",
            "target_role": "Spanish tenant" if is_spanish else "Tenant",
            "target_phone_label": "Tenant Phone",
            "missing_phone_label": "NO TENANT PHONE",
        }
    return {
        "target_track": "ec",
        "target_brand": "Grant Ellis Group",
        "target_role": "Landlord / owner",
        "target_phone_label": "Landlord Phone",
        "missing_phone_label": "NO LANDLORD PHONE",
    }


def _decorate_dashboard_rows(rows: list[dict], track: str, view: str) -> list[dict]:
    metadata = _target_metadata(track, view)
    return [{**row, **metadata} for row in rows]


def _overlay_dashboard_contact_data(rows: list[dict], track: str) -> list[dict]:
    case_numbers = [row["case_number"] for row in rows]
    if not case_numbers:
        return rows
    contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", track)
        .in_("case_number", case_numbers)
        .execute()
        .data
    )
    return _overlay_contact_rows(rows, contacts, clear_missing_contact=track == "ng")


def _get_ng_dashboard_leads(view: str, limit: int) -> list[dict]:
    ng_contacts = (
        _client.table("lead_contacts")
        .select(
            "case_number,track,phone,email,property_type,estimated_rent,"
            "language_hint,bland_status,ghl_contact_id"
        )
        .eq("track", "ng")
        .execute()
        .data
    )
    if not ng_contacts:
        return []

    # View-specific actionable filtering. Other views (commercial / held /
    # spanish_* / discarded) intentionally pass through unfiltered — they
    # still want everything in the bucket regardless of phone/bland state.
    if view == "ng_residential":
        ng_contacts = [c for c in ng_contacts if _is_ng_contact_actionable(c)]
    elif view == "ng_already_called":
        ng_contacts = [c for c in ng_contacts if _is_ng_contact_already_called(c)]
    if not ng_contacts:
        return []

    ng_case_numbers = [row["case_number"] for row in ng_contacts]
    query = _client.table("filings").select(_DASHBOARD_SELECT)
    query = _filter_dashboard_query(query, view)
    query = query.in_("case_number", ng_case_numbers)
    result = (
        query
        .order("court_date", desc=False, nullsfirst=False)
        .order("filing_date", desc=True)
        .limit(limit)
        .execute()
    )
    rows = _overlay_contact_rows(result.data, ng_contacts, clear_missing_contact=False)
    return _decorate_dashboard_rows(rows, "ng", view)


async def get_dashboard_leads(
    view: str = "residential_approved",
    limit: int = 500,
    track: str | None = None,
) -> list[dict]:
    def _query() -> list[dict]:
        effective_track = track or _track_for_dashboard_view(view)
        if effective_track == "ng":
            return _get_ng_dashboard_leads(view, limit)
        # EC: query filings directly, overlay EC contacts
        query = _client.table("filings").select(_DASHBOARD_SELECT)
        query = _filter_dashboard_query(query, view)
        result = (
            query
            .order("court_date", desc=False, nullsfirst=False)
            .order("filing_date", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data
        rows = _overlay_dashboard_contact_data(rows, effective_track)
        return _decorate_dashboard_rows(rows, effective_track, view)
    return await asyncio.to_thread(_query)


def _ec_counts_from_rows(rows: list[dict]) -> dict:
    counts = {
        "ec_residential": 0,
        "ec_commercial": 0,
        "ec_held": 0,
        "ec_discarded": 0,
    }
    for row in rows:
        bucket = row.get("lead_bucket")
        spanish = row.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved" and not spanish:
            counts["ec_residential"] += 1
        elif bucket == "commercial" and not spanish:
            counts["ec_commercial"] += 1
        elif bucket == "held":
            counts["ec_held"] += 1
        elif bucket == "discarded":
            counts["ec_discarded"] += 1
    return counts


def _ng_counts_from_contact_rows(rows: list[dict]) -> dict:
    """Tally NG-track dashboard counts. Applies the actionable predicate to
    residential_approved (so ng_residential matches the table), and adds
    ng_already_called for Bland-triggered / wrong_brand_review leads.

    Spanish, commercial, held, and discarded counts keep their original
    semantics (no phone/bland filtering) — those views still surface
    everything in their bucket.
    """
    counts = {
        "ng_residential": 0,
        "ng_commercial": 0,
        "ng_spanish_residential": 0,
        "ng_spanish_commercial": 0,
        "ng_held": 0,
        "ng_discarded": 0,
        "ng_already_called": 0,
    }
    for row in rows:
        filing = row.get("filings") or {}
        bucket = filing.get("lead_bucket")
        spanish = filing.get("language_hint") == "spanish_likely"
        if bucket == "residential_approved":
            if spanish:
                counts["ng_spanish_residential"] += 1
            elif _is_ng_contact_actionable(row):
                counts["ng_residential"] += 1
            if not spanish and _is_ng_contact_already_called(row):
                counts["ng_already_called"] += 1
        elif bucket == "commercial":
            if spanish:
                counts["ng_spanish_commercial"] += 1
            else:
                counts["ng_commercial"] += 1
        elif bucket == "held":
            counts["ng_held"] += 1
        elif bucket == "discarded":
            counts["ng_discarded"] += 1
    return counts


def _count_filings(bucket: str, spanish: bool | None = None) -> int:
    q = (
        _client.table("filings")
        .select("case_number", count="exact")
        .eq("lead_bucket", bucket)
    )
    if spanish is True:
        q = q.eq("language_hint", "spanish_likely")
    elif spanish is False:
        q = q.or_("language_hint.is.null,language_hint.neq.spanish_likely")
    return q.limit(1).execute().count or 0


async def get_dashboard_counts() -> dict:
    def _query() -> dict:
        ec_counts = {
            "ec_residential": _count_filings("residential_approved", spanish=False),
            "ec_commercial": _count_filings("commercial", spanish=False),
            "ec_held": _count_filings("held"),
            "ec_discarded": _count_filings("discarded"),
        }
        ng_rows = (
            _client.table("lead_contacts")
            .select(
                "case_number,phone,bland_status,"
                "filings(lead_bucket,language_hint)"
            )
            .eq("track", "ng")
            .limit(10000)
            .execute()
            .data
        )
        return {**ec_counts, **_ng_counts_from_contact_rows(ng_rows)}
    return await asyncio.to_thread(_query)


async def get_recent_metrics(limit: int = 10) -> list[dict]:
    def _query() -> list[dict]:
        result = (
            _client.table("run_metrics")
            .select("*")
            .order("run_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data
    return await asyncio.to_thread(_query)
