from __future__ import annotations

import asyncio
import logging
import os
import re
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
    dnc_service,
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
_AUTO_BLAND_CALLS_ENABLED = os.getenv("AUTO_BLAND_CALLS_ENABLED", "false").lower() == "true"

_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b",
    re.IGNORECASE,
)

SPANISH_LIKELY_TAG = "Spanish-Likely"


@dataclass(frozen=True)
class TrackResult:
    ghl_created: bool
    instantly_enrolled: bool = False
    instantly_error: str | None = None


def _is_business_name(name: str) -> bool:
    return bool(_BUSINESS_RE.search(name))


def _is_usable_address(address: str) -> bool:
    return bool(address and address.strip().lower() not in {"unknown", ""})


def _language_tags(contact: EnrichedContact) -> list[str]:
    if contact.track == "ng" and contact.language_hint == language_service.SPANISH_LIKELY:
        return [SPANISH_LIKELY_TAG]
    return []


def _has_contact_method(contact: EnrichedContact) -> bool:
    return bool(contact.phone or contact.email)


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
        return TrackResult(False)

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
        return TrackResult(False)

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
                return TrackResult(True)
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
        return TrackResult(False)

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
        return TrackResult(False)

    instantly_result = await instantly_service.enroll(contact)

    if contact.phone:
        dnc_decision = dnc_service.can_call(contact)
        if not dnc_decision.allowed:
            await dedup_service.set_bland_status(
                filing.case_number,
                contact.track,
                "blocked_dnc" if dnc_decision.status == "blocked" else "pending_dnc_review",
            )
            log.info(
                f"{filing.case_number} [{contact.track.upper()}] Bland blocked: "
                f"{dnc_decision.reason}"
            )
            return TrackResult(
                True,
                instantly_enrolled=instantly_result.enrolled,
                instantly_error=instantly_result.error,
            )

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
    )


async def _classify_and_store(filing: Filing, contact: EnrichedContact | None = None) -> str:
    outcome = classify_lead(
        state=filing.state,
        property_address=filing.property_address,
        filing_date=filing.filing_date,
        property_type=contact.property_type if contact else filing.property_type_hint,
        estimated_rent=contact.estimated_rent if contact else filing.claim_amount,
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

    m = dict(
        run_at=started_at.isoformat(),
        state=state,
        county=county,
        filings_received=len(filings),
        duplicates_skipped=0,
        address_skipped=0,
        batchdata_calls=0,
        phones_found=0,
        ghl_created=0,
        bland_triggered=0,
        instantly_enrolled=0,
    )

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

        if await _apply_rent_precheck(filing):
            m["address_skipped"] += 1
            continue

        enrich_tenant_flag = tenant_track_enabled and not _is_business_name(filing.tenant_name)
        if tenant_track_enabled and not enrich_tenant_flag:
            log.info(f"{filing.case_number} tenant track skipped: tenant looks like business")

        try:
            property_info = None
            property_lookup_calls = 0
            if filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1

            if landlord_track_enabled and enrich_tenant_flag:
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
                m["batchdata_calls"] += property_lookup_calls + 2
            elif enrich_tenant_flag:
                ng_contact = await batchdata_service.enrich_tenant(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ec_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
            elif landlord_track_enabled:
                ec_contact = await batchdata_service.enrich(
                    filing,
                    property_info=property_info,
                    lookup_property_if_missing=False,
                )
                ng_contact = None
                m["batchdata_calls"] += property_lookup_calls + 1
            else:
                m["batchdata_calls"] += property_lookup_calls
                log.info(
                    f"{filing.case_number} skipped: business name tenant and landlord track disabled"
                )
                continue
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
            if result.instantly_error:
                m.setdefault("instantly_failures", []).append(result.instantly_error)
            if result.instantly_enrolled:
                m["instantly_enrolled"] += 1

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    m["elapsed_seconds"] = elapsed
    log.info(
        f"Run complete in {elapsed:.1f}s: "
        f"received={m['filings_received']} dupes={m['duplicates_skipped']} "
        f"discarded={m['address_skipped']} batchdata={m['batchdata_calls']} "
        f"phones={m['phones_found']} ghl={m['ghl_created']}"
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
