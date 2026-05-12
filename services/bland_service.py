from __future__ import annotations

import logging
import os

import httpx

from models.contact import EnrichedContact

log = logging.getLogger(__name__)

BASE = "https://api.bland.ai"

_EC_AGENT_ID = os.getenv("BLAND_EC_AGENT_ID", "")
_NG_AGENT_ID = os.getenv("BLAND_NG_AGENT_ID", "")
_NG_SPANISH_AGENT_ID = os.getenv("BLAND_NG_SPANISH_AGENT_ID", "")
_EC_PHONE_NUMBER = os.getenv("BLAND_EC_PHONE_NUMBER", "")
_NG_PHONE_NUMBER = os.getenv("BLAND_NG_PHONE_NUMBER", "")
_NG_SPANISH_PHONE_NUMBER = os.getenv("BLAND_NG_SPANISH_PHONE_NUMBER", "")
_EC_CALLBACK_NUMBER = os.getenv("BLAND_EC_CALLBACK_PHONE_NUMBER", "")
_NG_CALLBACK_NUMBER = os.getenv("BLAND_NG_CALLBACK_PHONE_NUMBER", "")
_NG_SPANISH_CALLBACK_NUMBER = os.getenv("BLAND_NG_SPANISH_CALLBACK_PHONE_NUMBER", "")

_EC_VOICEMAIL_SCRIPT = (
    "Hi, this message is for {first_name}. This is Alex calling from Grant Ellis Group. "
    "We noticed a recent filing in {county} County associated with your property at "
    "{property_address}. If you need county-specific eviction documents prepared - "
    "notices, UD packages, or serving instructions - we deliver them in 24 hours "
    "starting at $297. Attorney reviewed and county specific. Call us back at "
    "{ec_phone} or visit grantellisgroup.com. That number again is {ec_phone}. "
    "Have a great day."
)

_NG_VOICEMAIL_SCRIPT = (
    "Hi, this message is for {first_name}. This is an important call from Vantage Defense Group. "
    "You may have recently received legal papers about your home. Do not ignore them - "
    "you have rights and you have options. We are here to help protect you and keep you "
    "in your home. Call us today at {ng_phone} for a free consultation. Someone is "
    "standing by right now to help you. If you prefer to continue in Spanish - "
    "hola {first_name}, le llama Vantage Defense Group. Usted tiene derechos. "
    "Estamos aqui para protegerle y ayudarle a quedarse en su hogar. Llamenos al "
    "{ng_phone}. La consulta es gratis y estamos aqui para usted ahora mismo. "
    "Again that number is {ng_phone}. We are on your side. Call us now."
)

_NG_SPANISH_VOICEMAIL_SCRIPT = (
    "Hola, este mensaje es para {first_name}. Le llama Vantage Defense Group. "
    "Es posible que usted haya recibido papeles legales sobre su hogar. "
    "No los ignore - usted tiene derechos y tiene opciones. "
    "Estamos aqui para protegerle y ayudarle a quedarse en su hogar. "
    "Llamenos hoy al {ng_phone} para una consulta gratuita. "
    "Alguien esta disponible ahora mismo para ayudarle. "
    "Ese numero es {ng_phone}. Estamos de su lado. Llamenos ahora."
)


def _headers() -> dict[str, str]:
    key = os.environ.get("BLAND_API_KEY", "")
    if not key:
        raise RuntimeError("BLAND_API_KEY not set")
    return {"authorization": key, "Content-Type": "application/json"}


def _is_spanish_likely(contact: EnrichedContact) -> bool:
    return contact.track == "ng" and contact.language_hint == "spanish_likely"


def _phone_number_for_contact(contact: EnrichedContact) -> str:
    if contact.track == "ec":
        return _EC_PHONE_NUMBER
    if _is_spanish_likely(contact):
        return _NG_SPANISH_PHONE_NUMBER or _NG_PHONE_NUMBER
    return _NG_PHONE_NUMBER


def _callback_number_for_contact(contact: EnrichedContact) -> str:
    from_number = _phone_number_for_contact(contact) or "[PHONE_NUMBER]"
    if contact.track == "ec":
        return _EC_CALLBACK_NUMBER or from_number
    if _is_spanish_likely(contact):
        return _NG_SPANISH_CALLBACK_NUMBER or _NG_CALLBACK_NUMBER or from_number
    return _NG_CALLBACK_NUMBER or from_number


def render_voicemail_script(contact: EnrichedContact) -> str:
    filing = contact.filing
    first_name = contact.contact_first_name
    callback = _callback_number_for_contact(contact)

    if contact.track == "ec":
        return _EC_VOICEMAIL_SCRIPT.format(
            first_name=first_name,
            county=filing.county,
            property_address=filing.property_address,
            ec_phone=callback,
        )

    script = _NG_SPANISH_VOICEMAIL_SCRIPT if _is_spanish_likely(contact) else _NG_VOICEMAIL_SCRIPT
    return script.format(
        first_name=first_name,
        ng_phone=callback,
    )


async def trigger_voicemail(contact: EnrichedContact) -> str:
    """Dispatch an outbound call via Bland.ai. Returns the Bland call_id."""
    if not contact.phone:
        raise ValueError("No phone number on contact - cannot trigger voicemail")

    filing = contact.filing
    is_ec = contact.track == "ec"

    is_spanish = _is_spanish_likely(contact)
    pathway_id = _EC_AGENT_ID if is_ec else (_NG_SPANISH_AGENT_ID if is_spanish else _NG_AGENT_ID)
    from_number = _EC_PHONE_NUMBER if is_ec else (
        _NG_SPANISH_PHONE_NUMBER if is_spanish and _NG_SPANISH_PHONE_NUMBER else _NG_PHONE_NUMBER
    )

    if not pathway_id:
        agent_var = "BLAND_EC_AGENT_ID" if is_ec else (
            "BLAND_NG_SPANISH_AGENT_ID" if is_spanish else "BLAND_NG_AGENT_ID"
        )
        raise RuntimeError(
            f"{agent_var} not set"
        )
    if not from_number:
        raise RuntimeError(
            f"{'BLAND_EC_PHONE_NUMBER' if is_ec else 'BLAND_NG_PHONE_NUMBER'} not set"
        )

    first_name = contact.contact_first_name
    voicemail_text = render_voicemail_script(contact)
    callback = _callback_number_for_contact(contact)

    payload: dict = {
        "phone_number": contact.phone,
        "from": from_number,
        "pathway_id": pathway_id,
        "request_data": {
            "first_name": first_name,
            "county": filing.county,
            "property_address": filing.property_address,
            "ec_phone" if is_ec else "ng_phone": callback,
            "language_hint": contact.language_hint or "",
        },
        "voicemail": {
            "action": "leave_message",
            "message": voicemail_text,
            # LLM-based detection is more accurate for IVRs and ambiguous pickups.
            "sensitive": True,
        },
        # Retry once after 4 hours if first attempt goes to voicemail.
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
