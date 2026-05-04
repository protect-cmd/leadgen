from __future__ import annotations
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import router
from services import dedup_service, batchdata_service, ghl_service, geocode_service

log = logging.getLogger(__name__)

GHL_EC_STAGE_ID = os.getenv("GHL_NEW_FILING_STAGE_ID", "")
GHL_NG_RESIDENTIAL_STAGE_ID = os.getenv("GHL_NG_NEW_FILING_STAGE_ID", "")
GHL_NG_COMMERCIAL_STAGE_ID = os.getenv("GHL_NG_COMMERCIAL_STAGE_ID", "")
_NG_ENABLED = bool(os.getenv("GHL_NG_LOCATION_ID", ""))
_BLAND_ENABLED = os.getenv("BLAND_ENABLED", "false").lower() == "true"

_BUSINESS_RE = re.compile(
    r"\b(LLC|INC|CORP|LTD|LP|LLP|PLLC|PROPERTIES|PROPERTY|MANAGEMENT|MGMT|"
    r"REALTY|INVESTMENTS|HOLDINGS|TRUST|PARTNERS|GROUP|ENTERPRISES|VENTURES)\b",
    re.IGNORECASE,
)


def _is_business_name(name: str) -> bool:
    return bool(_BUSINESS_RE.search(name))


def _is_usable_address(address: str) -> bool:
    return bool(address and address.strip().lower() not in {"unknown", ""})


async def _process_track(contact: EnrichedContact) -> None:
    """Route, push to GHL, and trigger Bland for one track (EC or NG)."""
    filing = contact.filing
    is_ec = contact.track == "ec"

    outcome = router.route_ec(contact) if is_ec else router.route_ng(contact)
    log.info(
        f"{filing.case_number} [{contact.track.upper()}] routed: "
        f"action={outcome.action} tag={outcome.tag}"
    )

    if outcome.action != "proceed":
        return

    if is_ec:
        stage_id = GHL_EC_STAGE_ID
    elif outcome.pipeline == "commercial":
        stage_id = GHL_NG_COMMERCIAL_STAGE_ID
    else:
        stage_id = GHL_NG_RESIDENTIAL_STAGE_ID

    try:
        ghl_id = await ghl_service.create_contact(contact, [outcome.tag], stage_id)
        await dedup_service.update_ghl_id(filing.case_number, ghl_id, contact.track)
        log.info(f"GHL contact created [{contact.track.upper()}]: {ghl_id}")
    except Exception as e:
        log.warning(f"GHL failed [{contact.track.upper()}] {filing.case_number}: {e}")
        return

    # Bland is never auto-fired — leads queue as 'pending' for manual approval in dashboard.
    if contact.phone:
        await dedup_service.set_bland_status(filing.case_number, contact.track, "pending")
        log.info(f"{filing.case_number} [{contact.track.upper()}] queued for Bland review")


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
            log.info(f"Duplicate — skipping: {filing.case_number}")
            m["duplicates_skipped"] += 1
            continue

        await dedup_service.insert_filing(filing)

        # Normalize address; gate on result to avoid wasting BatchData credits.
        normalized = await geocode_service.normalize_address(filing.property_address)
        if normalized:
            log.debug(f"{filing.case_number} address normalized: {normalized}")
            filing.property_address = normalized
        elif not _is_usable_address(filing.property_address):
            log.warning(f"{filing.case_number} skipped — unusable address: {filing.property_address!r}")
            m["address_skipped"] += 1
            continue

        # Skip NG enrichment if tenant name looks like a business entity.
        enrich_ng = _NG_ENABLED and not _is_business_name(filing.tenant_name)
        if _NG_ENABLED and not enrich_ng:
            log.info(f"{filing.case_number} NG skipped — tenant looks like business: {filing.tenant_name!r}")

        # Enrich EC always; NG only when tenant is a real person.
        try:
            if enrich_ng:
                ec_contact, ng_contact = await asyncio.gather(
                    batchdata_service.enrich(filing),
                    batchdata_service.enrich_tenant(filing),
                )
                m["batchdata_calls"] += 2
            else:
                ec_contact = await batchdata_service.enrich(filing)
                ng_contact = None
                m["batchdata_calls"] += 1
        except Exception as e:
            log.warning(f"Enrichment failed for {filing.case_number}: {e}")
            continue

        if ec_contact.phone:
            m["phones_found"] += 1

        # EC enrichment has rent/property_type — store it as the canonical record.
        await dedup_service.update_enrichment(ec_contact)

        tasks = [_process_track(ec_contact)]
        if ng_contact is not None:
            tasks.append(_process_track(ng_contact))
        await asyncio.gather(*tasks)

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    m["elapsed_seconds"] = elapsed
    log.info(
        f"Run complete in {elapsed:.1f}s — "
        f"received={m['filings_received']} dupes={m['duplicates_skipped']} "
        f"addr_skip={m['address_skipped']} batchdata={m['batchdata_calls']} "
        f"phones={m['phones_found']}"
    )
    try:
        await dedup_service.write_run_metrics(m)
    except Exception as e:
        log.warning(f"Failed to write run metrics: {e}")
