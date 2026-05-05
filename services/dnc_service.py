from __future__ import annotations

from dataclasses import dataclass

from models.contact import EnrichedContact


@dataclass(frozen=True)
class DncDecision:
    allowed: bool
    status: str
    reason: str


def normalize_status(status: str | None) -> str:
    value = (status or "unknown").strip().lower()
    if value in {"clear", "blocked"}:
        return value
    return "unknown"


def can_call(contact: EnrichedContact) -> DncDecision:
    if not contact.phone:
        return DncDecision(False, "unknown", "No phone number")

    status = normalize_status(contact.dnc_status)
    if status == "clear":
        return DncDecision(True, status, "DNC clear")
    if status == "blocked":
        return DncDecision(False, status, "Phone is DNC blocked")
    return DncDecision(False, status, "DNC status unknown")
