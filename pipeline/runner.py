from __future__ import annotations
import logging
import os
from models.filing import Filing
from models.contact import EnrichedContact
from pipeline import router
from services import dedup_service, batchdata_service, ghl_service, bland_service

log = logging.getLogger(__name__)

GHL_NEW_FILING_STAGE_ID = os.getenv("GHL_NEW_FILING_STAGE_ID", "")
GHL_NG_COMMERCIAL_STAGE_ID = os.getenv("GHL_NG_COMMERCIAL_STAGE_ID", "")


async def run(filings: list[Filing]) -> None:
    log.info(f"Runner received {len(filings)} filings")

    for filing in filings:
        log.info(f"Processing {filing.case_number}")

        if await dedup_service.is_duplicate(filing.case_number):
            log.info(f"Duplicate — skipping: {filing.case_number}")
            continue

        await dedup_service.insert_filing(filing)

        try:
            contact: EnrichedContact = await batchdata_service.enrich(filing)
        except NotImplementedError as e:
            log.warning(f"BatchData not implemented — skipping enrichment: {e}")
            continue

        outcome = router.route(contact)
        await dedup_service.update_routing(filing.case_number, outcome)
        log.info(f"{filing.case_number} routed: action={outcome.action} tag={outcome.tag}")

        if outcome.action != "proceed":
            continue

        stage_id = (
            GHL_NG_COMMERCIAL_STAGE_ID
            if outcome.pipeline == "commercial"
            else GHL_NEW_FILING_STAGE_ID
        )

        try:
            ghl_id = await ghl_service.create_contact(contact, [outcome.tag], stage_id)
            await dedup_service.update_ghl_id(filing.case_number, ghl_id)
            log.info(f"GHL contact created: {ghl_id}")
        except NotImplementedError as e:
            log.warning(f"GHL not implemented — skipping contact creation: {e}")
            continue

        if contact.phone:
            try:
                await bland_service.trigger_voicemail(
                    contact.phone,
                    filing.tenant_name,
                    filing.property_address,
                )
                await dedup_service.mark_bland_triggered(filing.case_number)
                log.info(f"Bland voicemail triggered for {filing.case_number}")
            except NotImplementedError as e:
                log.warning(f"Bland not implemented — skipping voicemail: {e}")
