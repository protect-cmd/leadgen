from __future__ import annotations

import asyncio
import logging
import os
import re
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
    notification_service,
)

log = logging.getLogger(__name__)

GHL_EC_STAGE_ID = os.getenv("GHL_NEW_FILING_STAGE_ID", "")
GHL_EC_REVIEW_STAGE_ID = os.getenv("GHL_EC_REVIEW_STAGE_ID", "")
GHL_NG_RESIDENTIAL_STAGE_ID = os.getenv("GHL_NG_NEW_FILING_STAGE_ID", "")
GHL_NG_COMMERCIAL_STAGE_ID = os.getenv("GHL_NG_COMMERCIAL_STAGE_ID", "")
_NG_ENABLED = bool(os.getenv("GHL_NG_LOCATION_ID", ""))
_AUTO_BLAND_CALLS_ENABLED = os.getenv("AUTO_BLAND_CALLS_ENABLED", "false").lower() == "true"

_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b",
    re.IGNORECASE,
)


def _is_business_name(name: str) -> bool:
    return bool(_BUSINESS_RE.search(name))


def _is_usable_address(address: str) -> bool:
    return bool(address and address.strip().lower() not in {"unknown", ""})


async def _process_track(contact: EnrichedContact) -> bool:
    """Route, push to GHL, and queue/trigger Bland for one EC or NG track."""
    filing = contact.filing
    is_ec = contact.track == "ec"
    outcome = router.route_ec(contact) if is_ec else router.route_ng(contact)

    log.info(
        f"{filing.case_number} [{contact.track.upper()}] routed: "
        f"action={outcome.action} tag={outcome.tag}"
    )

    if outcome.action == "skip":
        return False

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
                return True
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
        return False

    if outcome.pipeline == "commercial":
        stage_id = GHL_NG_COMMERCIAL_STAGE_ID
        tags = [outcome.tag, "High-Priority"]
    elif is_ec:
        stage_id = GHL_EC_STAGE_ID
        tags = [outcome.tag]
    else:
        stage_id = GHL_NG_RESIDENTIAL_STAGE_ID
        tags = [outcome.tag]

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
        return False

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
            return True

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

    return True


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


async def run(filings: list[Filing], state: str = "", county: str = "") -> None:
    started_at = datetime.now(timezone.utc)
    log.info(f"Runner received {len(filings)} filings")

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
    )

    for filing in filings:
        log.info(f"Processing {filing.case_number}")

        if await dedup_service.is_duplicate(filing.case_number):
            log.info(f"Duplicate skipped: {filing.case_number}")
            m["duplicates_skipped"] += 1
            continue

        await dedup_service.insert_filing(filing)

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

        enrich_ng = _NG_ENABLED and not _is_business_name(filing.tenant_name)
        if _NG_ENABLED and not enrich_ng:
            log.info(f"{filing.case_number} NG skipped: tenant looks like business")

        try:
            property_info = None
            property_lookup_calls = 0
            if filing.property_type_hint is None:
                property_info = await batchdata_service.lookup_property_info(filing)
                property_lookup_calls = 1

            if enrich_ng:
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

        if ec_contact.phone:
            m["phones_found"] += 1

        await dedup_service.update_enrichment(ec_contact)

        lead_bucket = await _classify_and_store(filing, ec_contact)
        if lead_bucket == "discarded":
            log.info(f"{filing.case_number} discarded after enrichment")
            m["address_skipped"] += 1
            continue

        tasks = [_process_track(ec_contact)]
        if ng_contact is not None:
            tasks.append(_process_track(ng_contact))
        results = await asyncio.gather(*tasks)
        m["ghl_created"] += sum(1 for created in results if created)

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
