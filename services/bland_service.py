from __future__ import annotations

import logging
import os

import httpx

from models.contact import EnrichedContact

log = logging.getLogger(__name__)

BASE = "https://api.bland.ai"

_EC_AGENT_ID = os.getenv("BLAND_EC_AGENT_ID", "")
_NG_AGENT_ID = os.getenv("BLAND_NG_AGENT_ID", "")
_EC_PHONE_NUMBER = os.getenv("BLAND_EC_PHONE_NUMBER", "")
_NG_PHONE_NUMBER = os.getenv("BLAND_NG_PHONE_NUMBER", "")

_EC_VOICEMAIL_SCRIPT = (
    "Hi, this message is for {first_name}. My name is Alex calling from EvictionCommand. "
    "We noticed a recent unlawful detainer filing in {county} County associated with your "
    "property at {property_address}. We specialize in preparing county-specific eviction "
    "documents — notices, UD packages, and serving instructions — delivered in 24 hours "
    "starting at $297. If you still need documents for your case, call us back at "
    "{ec_phone} or visit evictioncommand.com. Again that number is {ec_phone}. "
    "Have a great day."
)

_NG_VOICEMAIL_SCRIPT = (
    "Hi, this message is for {first_name}. My name is calling from Nobles and Greyson. "
    "We understand you may have recently received an eviction notice at {property_address}. "
    "We help tenants respond to eviction filings and in most cases we can keep you in your "
    "home for four to five months while your case works through the court. There is no "
    "obligation to speak with us — we just want to make sure you know your options before "
    "your response deadline. Please call us back at {ng_phone} for a free consultation. "
    "That number again is {ng_phone}. We are here to help."
)


def _headers() -> dict[str, str]:
    key = os.environ.get("BLAND_API_KEY", "")
    if not key:
        raise RuntimeError("BLAND_API_KEY not set")
    return {"authorization": key, "Content-Type": "application/json"}


async def trigger_voicemail(contact: EnrichedContact) -> str:
    """Dispatch an outbound call via Bland.ai. Returns the Bland call_id."""
    if not contact.phone:
        raise ValueError("No phone number on contact — cannot trigger voicemail")

    filing = contact.filing
    is_ec = contact.track == "ec"

    pathway_id = _EC_AGENT_ID if is_ec else _NG_AGENT_ID
    from_number = _EC_PHONE_NUMBER if is_ec else _NG_PHONE_NUMBER

    if not pathway_id:
        raise RuntimeError(
            f"{'BLAND_EC_AGENT_ID' if is_ec else 'BLAND_NG_AGENT_ID'} not set"
        )
    if not from_number:
        raise RuntimeError(
            f"{'BLAND_EC_PHONE_NUMBER' if is_ec else 'BLAND_NG_PHONE_NUMBER'} not set"
        )

    first_name = contact.contact_first_name

    if is_ec:
        voicemail_text = _EC_VOICEMAIL_SCRIPT.format(
            first_name=first_name,
            county=filing.county,
            property_address=filing.property_address,
            ec_phone=from_number,
        )
    else:
        voicemail_text = _NG_VOICEMAIL_SCRIPT.format(
            first_name=first_name,
            property_address=filing.property_address,
            ng_phone=from_number,
        )

    payload: dict = {
        "phone_number": contact.phone,
        "from": from_number,
        "pathway_id": pathway_id,
        "request_data": {
            "first_name": first_name,
            "county": filing.county,
            "property_address": filing.property_address,
            "ec_phone" if is_ec else "ng_phone": from_number,
        },
        "voicemail": {
            "action": "leave_message",
            "message": voicemail_text,
            # LLM-based detection — more accurate for IVRs and ambiguous pickups
            "sensitive": True,
        },
        # Retry once after 4 hours if first attempt goes to voicemail
        "retry": {
            "wait": 14400,
            "voicemail_action": "leave_message",
            "voicemail_message": voicemail_text,
        },
        "record": True,
        "max_duration": 3,
        "metadata": {
            "case_number": filing.case_number,
            "track": contact.track,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE}/v1/calls",
            json=payload,
            headers=_headers(),
        )

    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Bland call dispatch failed {r.status_code}: {r.text[:300]}"
        )

    call_id: str = r.json().get("call_id", "")
    log.info(
        f"Bland ({contact.track.upper()}) call dispatched: "
        f"call_id={call_id} to={contact.phone} case={filing.case_number}"
    )
    return call_id
