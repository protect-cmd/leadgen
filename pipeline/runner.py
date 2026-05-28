from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from models.contact import EnrichedContact
from models.filing import Filing
from pipeline import router
from pipeline.qualification import classify_lead
from services import (
    batchdata_service,
    bland_service,
    dedup_service,
    geocode_service,
    ghl_service,
    instantly_service,
    language_service,
    notification_service,
    rent_estimate_service,
)

log = logging.getLogger(__name__)

GHL_EC_STAGE_ID = os.getenv("GHL_NEW_FILING_STAGE_ID", "")
GHL_EC_REVIEW_STAGE_ID = os.getenv("GHL_EC_REVIEW_STAGE_ID", "")
GHL_NG_RESIDENTIAL_STAGE_ID = os.getenv("GHL_NG_NEW_FILING_STAGE_ID", "")
GHL_NG_COMMERCIAL_STAGE_ID = os.getenv("GHL_NG_COMMERCIAL_STAGE_ID", "")
GHL_NG_REVIEW_STAGE_ID = os.getenv("GHL_NG_REVIEW_STAGE_ID", "")
_AUTO_BLAND_CALLS_ENABLED = os.getenv("AUTO_BLAND_CALLS_ENABLED", "false").lower() == "true"
_NG_REVIEW_STATUSES = frozenset({"name_mismatch", "ambiguous"})
_CAPTURE_EXPANDED_ZIPS = os.getenv("CAPTURE_EXPANDED_ZIPS", "true").lower() == "true"
_BYPASS_ZIP_FILTER = os.getenv("BYPASS_ZIP_FILTER", "false").lower() == "true"
_ENRICHMENT_WINDOW_DAYS = int(os.getenv("ENRICHMENT_WINDOW_DAYS", "10"))

SPANISH_LIKELY_TAG = "Spanish-Likely"


@dataclass(frozen=True)
class TrackResult:
    ghl_created: bool
    instantly_enrolled: bool = False
    instantly_error: str | None = None
    track: str = ""
    is_review: bool = False


def _is_usable_address(address: str) -> bool:
    return bool(address and address.strip().lower() not in {"unknown", ""})


def _language_tags(contact: EnrichedContact) -> list[str]:
    if contact.track == "ng" and contact.language_hint == language_service.SPANISH_LIKELY:
        return [SPANISH_LIKELY_TAG]
    return []


def _has_contact_method(contact: EnrichedContact) -> bool:
    return bool(contact.phone or contact.email)


async def _maybe_llm_recover(filing: Filing, *, reason: str) -> bool:
    """Try to salvage a gate-rejected filing via the LLM recovery service.

    Mutates filing.tenant_name / filing.property_address in place when the LLM
    returns a high-confidence cleanup that re-passes the rejecting gate.
    Returns True if the lead was recovered, False otherwise.

    Hard fail-closed: any LLM error / low confidence / cleaned output that
    still fails the gate → return False so the caller skips as usual.
    """
    from services import llm_recovery_service
    from pipeline import gates as _gates

    if not llm_recovery_service.is_enabled():
        return False

    result = await llm_recovery_service.recover(
        raw_name=filing.tenant_name or "",
        raw_address=filing.property_address or "",
        state=filing.state or "",
    )
    if result.confidence < llm_recovery_service.RECOVERY_CONFIDENCE_THRESHOLD:
        return False
    if result.skip_reason:
        log.info(
            f"{filing.case_number} LLM declined to recover ({reason}): {result.skip_reason}"
        )
        return False

    if reason == "address":
        if not (result.street and result.zip):
            return False
        candidate = result.formatted_address
        if not _gates.gate_address(candidate):
            return False
        filing.property_address = candidate
        return True
    if reason == "name":
        if not (result.first and result.last):
            return False
        candidate = result.formatted_name
        if not _gates.gate_name(candidate):
            return False
        filing.tenant_name = candidate
        return True
    return False


async def _process_track(contact: EnrichedContact) -> TrackResult:
    """Route, push to GHL, queue/trigger Bland, and enroll in Instantly for one track.

    Returns structured per-track processing metrics.
    """
    filing = contact.filing
    is_ec = contact.track == "ec"
    outcome = router.route_ec(contact) if is_ec else router.route_ng(contact)

    log.info(
        f"{filing.case_number} [{contact.track.upper()}] routed: "
        f"action={outcome.action} tag={outcome.tag}"
    )

    if outcome.action == "skip":
        return TrackResult(False, track=contact.track)

    if not _has_contact_method(contact):
        await dedup_service.set_bland_status(
            filing.case_number,
            contact.track,
            "missing_contact_data",
        )
        log.info(
            f"{filing.case_number} [{contact.track.upper()}] skipped: "
            "no phone or email from enrichment"
        )
        return TrackResult(False, track=contact.track)

    # NG review-stage leads (SearchBug returned name_mismatch or ambiguous).
    # A human verifies the match before outreach — skip Instantly and Bland.
    if not is_ec and contact.searchbug_status in _NG_REVIEW_STATUSES:
        if GHL_NG_REVIEW_STAGE_ID and contact.phone:
            review_tag = (
                "Review-NameMismatch"
                if contact.searchbug_status == "name_mismatch"
                else "Review-Ambiguous"
            )
            try:
                ghl_id = await ghl_service.create_contact(
                    contact,
                    [review_tag] + _language_tags(contact),
                    GHL_NG_REVIEW_STAGE_ID,
                )
                await dedup_service.update_ghl_id(filing.case_number, ghl_id, contact.track)
                log.info(
                    f"GHL review contact created [NG]: {ghl_id} "
                    f"(searchbug_status={contact.searchbug_status})"
                )
                return TrackResult(True, track=contact.track, is_review=True)
            except Exception as e:
                log.warning(f"GHL review failed [NG] {filing.case_number}: {e}")
                await notification_service.send_job_error(
                    job=f"{filing.state}/{filing.county}",
                    stage="ghl_review_ng",
                    error=e,
                )
        return TrackResult(False, track=contact.track, is_review=True)

    if outcome.action == "flag" and is_ec:
        if GHL_EC_REVIEW_STAGE_ID and contact.phone:
            try:
                ghl_id = await ghl_service.create_contact(
                    contact,
                    [outcome.tag],
                    GHL_EC_REVIEW_STAGE_ID,
                )
                await dedup_service.update_ghl_id(filing.case_number, ghl_id, contact.track)
                log.info(f"GHL review contact created [{contact.track.upper()}]: {ghl_id}")
                return TrackResult(True, track=contact.track)
            except Exception as e:
                log.warning(
                    f"GHL review failed [{contact.track.upper()}] "
                    f"{filing.case_number}: {e}"
                )
                await notification_service.send_job_error(
                    job=f"{filing.state}/{filing.county}",
                    stage=f"ghl_review_{contact.track}",
                    error=e,
                )
        return TrackResult(False, track=contact.track)

    if outcome.pipeline == "commercial":
        stage_id = GHL_EC_STAGE_ID if is_ec else GHL_NG_COMMERCIAL_STAGE_ID
        tags = [outcome.tag, "High-Priority"]
    elif is_ec:
        stage_id = GHL_EC_STAGE_ID
        tags = [outcome.tag]
    else:
        stage_id = GHL_NG_RESIDENTIAL_STAGE_ID
        tags = [outcome.tag]
    tags.extend(_language_tags(contact))

    # DNC removed per 2026-05-28 spec — phone contacts proceed unconditionally.
    try:
        ghl_id = await ghl_service.create_contact(contact, tags, stage_id)
        await dedup_service.update_ghl_id(filing.case_number, ghl_id, contact.track)
        log.info(f"GHL contact created [{contact.track.upper()}]: {ghl_id}")
    except Exception as e:
        log.warning(f"GHL failed [{contact.track.upper()}] {filing.case_number}: {e}")
        await notification_service.send_job_error(
            job=f"{filing.state}/{filing.county}",
            stage=f"ghl_create_{contact.track}",
            error=e,
        )
        return TrackResult(False, track=contact.track)

    instantly_result = await instantly_service.enroll(contact)

    if contact.phone:
        if _AUTO_BLAND_CALLS_ENABLED:
            try:
                call_id = await bland_service.trigger_voicemail(contact)
                await dedup_service.set_bland_status(
                    filing.case_number,
                    contact.track,
                    "triggered",
                    call_id=call_id,
                )
                log.info(f"Bland auto-call triggered [{contact.track.upper()}]: {call_id}")
            except Exception as e:
                await dedup_service.set_bland_status(filing.case_number, contact.track, "pending")
                log.warning(
                    f"Bland auto-call failed [{contact.track.upper()}] "
                    f"{filing.case_number}: {e}"
                )
                await notification_service.send_job_error(
                    job=f"{filing.state}/{filing.county}",
                    stage=f"bland_{contact.track}",
                    error=e,
                )
        else:
            await dedup_service.set_bland_status(filing.case_number, contact.track, "pending")
            log.info(f"{filing.case_number} [{contact.track.upper()}] queued for Bland review")

    return TrackResult(
        True,
        instantly_enrolled=instantly_result.enrolled,
        instantly_error=instantly_result.error,
        track=contact.track,
    )


async def _classify_and_store(filing: Filing, contact: EnrichedContact | None = None) -> str:
    outcome = classify_lead(
        state=filing.state,
        property_address=filing.property_address,
        filing_date=filing.filing_date,
        property_type=contact.property_type if contact else filing.property_type_hint,
        estimated_rent=contact.estimated_rent if contact else filing.claim_amount,
        capture_expanded=_CAPTURE_EXPANDED_ZIPS,
        bypass_zip_filter=_BYPASS_ZIP_FILTER,
    )
    await dedup_service.update_classification(filing.case_number, outcome)
    return outcome.lead_bucket


def _should_precheck_rent(filing: Filing) -> bool:
    property_type = (filing.property_type_hint or "").strip().lower()
    return (
        rent_estimate_service.is_enabled()
        and filing.claim_amount is None
        and property_type == "residential"
    )


async def _apply_rent_precheck(filing: Filing) -> bool:
    if not _should_precheck_rent(filing):
        return False

    estimated_rent = await rent_estimate_service.estimate_rent(filing)
    if estimated_rent is None:
        return False

    outcome = classify_lead(
        state=filing.state,
        property_address=filing.property_address,
        filing_date=filing.filing_date,
        property_type=filing.property_type_hint,
        estimated_rent=estimated_rent,
        capture_expanded=_CAPTURE_EXPANDED_ZIPS,
        bypass_zip_filter=_BYPASS_ZIP_FILTER,
    )
    await dedup_service.update_classification(filing.case_number, outcome)

    if outcome.lead_bucket == "discarded":
        log.info(
            "%s discarded by rent precheck before BatchData: estimated_rent=%.2f",
            filing.case_number,
            estimated_rent,
        )
        return True

    log.info(
        "%s passed rent precheck before BatchData: estimated_rent=%.2f",
        filing.case_number,
        estimated_rent,
    )
    return False


async def run(filings: list[Filing], state: str = "", county: str = "") -> None:
    started_at = datetime.now(timezone.utc)
    log.info(f"Runner received {len(filings)} filings")

    tenant_track_enabled = os.getenv("TENANT_TRACK_ENABLED", "true").lower() == "true"
    landlord_track_enabled = os.getenv("LANDLORD_TRACK_ENABLED", "false").lower() == "true"
    if not tenant_track_enabled and not landlord_track_enabled:
        raise RuntimeError(
            "Invalid configuration: TENANT_TRACK_ENABLED and LANDLORD_TRACK_ENABLED "
            "cannot both be false. Set at least one to 'true'."
        )

    started_searchbug_count = 0
    try:
        from services.enrichment_cache import get_cache as _get_sb_cache
        _sb_cache = _get_sb_cache()
        # Snapshot today's SearchBug count so we can report this run's delta.
        with __import__("sqlite3").connect(_sb_cache._db_path) as _con:
            _row = _con.execute(
                "SELECT count FROM daily_cap WHERE date=?",
                (datetime.now(timezone.utc).date().isoformat(),),
            ).fetchone()
            started_searchbug_count = _row[0] if _row else 0
    except Exception:
        pass

    m = dict(
        run_at=started_at.isoformat(),
        state=state,
        county=county,
        filings_received=len(filings),
        duplicates_skipped=0,
        address_skipped=0,
        captured=0,
        gate_out_of_window=0,
        gate_overdue=0,
        gate_invalid_address=0,
        gate_bad_name=0,
        gate_existing_phone=0,
        gate_duplicate_in_run=0,
        gate_llm_recovered=0,
        batchdata_calls=0,
        ng_phones_pushed=0,
        phones_found=0,
        ghl_created=0,
        bland_triggered=0,
        instantly_enrolled=0,
        ng_review_pushed=0,
    )

    from datetime import date as _date
    from pipeline import gates as _gates
    from services.name_utils import parse_name as _parse_name
    from services.searchbug_service import query_street_address as _qsa
    from pipeline.qualification import extract_property_zip as _ezip

    _today = _date.today()
    _seen_queries: set[str] = set()

    for filing in filings:
        log.info(f"Processing {filing.case_number}")

        if await dedup_service.is_duplicate(filing.case_number):
            log.info(f"Duplicate skipped: {filing.case_number}")
            m["duplicates_skipped"] += 1
            continue

        await dedup_service.insert_filing(filing)

        language_hint = language_service.language_hint_for_name(filing.tenant_name)
        if language_hint:
            await dedup_service.update_language_hint(filing.case_number, language_hint)

        normalized = await geocode_service.normalize_address(filing.property_address)
        if normalized:
            log.debug(f"{filing.case_number} address normalized: {normalized}")
            filing.property_address = normalized
        elif not _is_usable_address(filing.property_address):
            log.warning(f"{filing.case_number} skipped: unusable address {filing.property_address!r}")
            await _classify_and_store(filing)
            m["address_skipped"] += 1
            continue

        lead_bucket = await _classify_and_store(filing)
        if lead_bucket == "discarded":
            log.info(f"{filing.case_number} discarded before enrichment")
            m["address_skipped"] += 1
            continue
        if lead_bucket == "captured":
            log.info(f"{filing.case_number} captured (off-allowlist ZIP); no enrichment")
            m["captured"] += 1
            continue

        if await _apply_rent_precheck(filing):
            m["address_skipped"] += 1
            continue

        # 9-gate filter before any paid enrichment call. Each gate increments a
        # run metric on miss so telemetry shows where leads are dropping.
        if not _gates.gate_filing_window(filing.filing_date, _today, _ENRICHMENT_WINDOW_DAYS):
            log.info(f"{filing.case_number} skipped: out of filing window")
            m["gate_out_of_window"] += 1
            continue
        if not _gates.gate_court_date(filing.court_date, _today):
            log.info(f"{filing.case_number} skipped: court_date overdue")
            m["gate_overdue"] += 1
            continue
        if not _gates.gate_address(filing.property_address):
            recovered = await _maybe_llm_recover(filing, reason="address")
            if recovered:
                m["gate_llm_recovered"] = m.get("gate_llm_recovered", 0) + 1
                log.info(f"{filing.case_number} LLM-recovered address: {filing.property_address!r}")
            else:
                log.info(f"{filing.case_number} skipped: invalid address")
                m["gate_invalid_address"] += 1
                continue
        if not _gates.gate_name(filing.tenant_name):
            recovered = await _maybe_llm_recover(filing, reason="name")
            if recovered:
                m["gate_llm_recovered"] = m.get("gate_llm_recovered", 0) + 1
                log.info(f"{filing.case_number} LLM-recovered name: {filing.tenant_name!r}")
            else:
                log.info(f"{filing.case_number} skipped: bad tenant name")
                m["gate_bad_name"] += 1
                continue

        if await dedup_service.has_ng_phone(filing.case_number):
            log.info(f"{filing.case_number} skipped: tenant phone already in lead_contacts")
            m["gate_existing_phone"] = m.get("gate_existing_phone", 0) + 1
            continue

        _first, _last = _parse_name(filing.tenant_name)
        _street = _qsa(filing.property_address)
        _zip = _ezip(filing.property_address) or ""
        if not _gates.gate_query_dedup(_first, _last, _street, _zip, _seen_queries):
            log.info(f"{filing.case_number} skipped: duplicate query in run")
            m["gate_duplicate_in_run"] += 1
            continue

        # gate_name (upstream) already rejects business-named tenants, so any
        # filing that reaches here is safe for the tenant track when enabled.
        try:
            property_info = None
            property_lookup_calls = 0
            # Only the landlord track needs BatchData's property lookup (it informs
            # commercial-vs-residential routing for the owner skip-trace). In
            # tenant-only mode we infer property_type from notice_type + tenant_name
            # heuristics — saves a third-party call per filing.
            if landlord_track_enabled and filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1
            elif filing.property_type_hint is None:
                from services.name_utils import infer_property_type
                filing.property_type_hint = infer_property_type(filing)

            if landlord_track_enabled and tenant_track_enabled:
                ec_contact, ng_contact = await asyncio.gather(
                    batchdata_service.enrich(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                    batchdata_service.enrich_tenant(
                        filing,
                        property_info=property_info,
                        lookup_property_if_missing=False,
                    ),
                )
                # enrich (EC) makes 1 BatchData skip-trace; enrich_tenant (NG)
                # no longer calls BatchData — it goes straight to SearchBug.
                m["batchdata_calls"] += property_lookup_calls + 1
            elif tenant_track_enabled:
                ng_contact = await batchdata_service.enrich_tenant(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ec_contact = None
                # No BatchData skip-trace for tenant track — SearchBug only.
                m["batchdata_calls"] += property_lookup_calls
            else:
                ec_contact = await batchdata_service.enrich(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ng_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
        except Exception as e:
            log.warning(f"Enrichment failed for {filing.case_number}: {e}")
            await notification_service.send_job_error(
                job=f"{state or filing.state}/{county or filing.county}",
                stage="batchdata_enrichment",
                error=e,
            )
            continue

        if ec_contact is not None:
            ec_contact.language_hint = language_hint
            if ec_contact.phone:
                m["phones_found"] += 1
            await dedup_service.update_enrichment(ec_contact)
        if ng_contact is not None:
            ng_contact.language_hint = language_hint
            if ng_contact.phone:
                m["phones_found"] += 1
            await dedup_service.update_enrichment(ng_contact)

        classify_contact = ec_contact or ng_contact
        lead_bucket = await _classify_and_store(filing, classify_contact)
        if lead_bucket == "discarded":
            log.info(f"{filing.case_number} discarded after enrichment")
            m["address_skipped"] += 1
            continue

        tasks = []
        if ec_contact is not None:
            tasks.append(_process_track(ec_contact))
        if ng_contact is not None:
            tasks.append(_process_track(ng_contact))
        results = await asyncio.gather(*tasks)
        for result in results:
            if result.ghl_created:
                m["ghl_created"] += 1
                if result.track == "ng":
                    if result.is_review:
                        m["ng_review_pushed"] += 1
                    else:
                        m["ng_phones_pushed"] += 1
            if result.instantly_error:
                m.setdefault("instantly_failures", []).append(result.instantly_error)
            if result.instantly_enrolled:
                m["instantly_enrolled"] += 1

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    m["elapsed_seconds"] = elapsed

    # Compute this run's SearchBug call delta vs. the start-of-run snapshot.
    try:
        from services.enrichment_cache import get_cache as _get_sb_cache_end
        _sb_cache = _get_sb_cache_end()
        with __import__("sqlite3").connect(_sb_cache._db_path) as _con:
            _row = _con.execute(
                "SELECT count FROM daily_cap WHERE date=?",
                (datetime.now(timezone.utc).date().isoformat(),),
            ).fetchone()
            ended_count = _row[0] if _row else 0
            m["searchbug_calls"] = max(0, ended_count - started_searchbug_count)
            m["searchbug_daily_total"] = ended_count
    except Exception:
        m["searchbug_calls"] = 0

    log.info(
        f"Run complete in {elapsed:.1f}s: "
        f"received={m['filings_received']} dupes={m['duplicates_skipped']} "
        f"discarded={m['address_skipped']} batchdata={m['batchdata_calls']} "
        f"phones={m['phones_found']} ghl={m['ghl_created']} "
        f"ng_pushed={m['ng_phones_pushed']} "
        f"searchbug_calls={m.get('searchbug_calls', 0)}"
    )
    try:
        await dedup_service.write_run_metrics(m)
    except Exception as e:
        log.warning(f"Failed to write run metrics: {e}")
        await notification_service.send_job_error(
            job=f"{state}/{county}",
            stage="write_run_metrics",
            error=e,
        )
    instantly_failures: list[str] = m.pop("instantly_failures", [])
    if instantly_failures:
        summary = "\n".join(instantly_failures[:20])
        if len(instantly_failures) > 20:
            summary += f"\n…and {len(instantly_failures) - 20} more"
        try:
            await notification_service.send_alert(
                "Instantly enrollment errors",
                f"{len(instantly_failures)} lead(s) failed to enroll:\n{summary}",
                tags={"job": f"{state}/{county}"},
            )
        except Exception as e:
            log.warning(f"Failed to send Instantly error alert: {e}")

    try:
        await notification_service.send_run_summary(
            m,
            auto_bland_enabled=_AUTO_BLAND_CALLS_ENABLED,
        )
    except Exception as e:
        log.warning(f"Failed to send run summary notification: {e}")


