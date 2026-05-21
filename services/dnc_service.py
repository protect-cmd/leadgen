from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from models.contact import EnrichedContact
from services.ftc_dnc_registry import FtcDncCheckResult, FtcDncRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DncDecision:
    allowed: bool
    status: str
    reason: str


_registry: FtcDncRegistry | None = None
_registry_load_attempted = False


def _load_registry() -> FtcDncRegistry | None:
    """Load the FTC DNC registry once per process from FTC_DNC_DB_PATH.

    Returns None if the env var is unset or the path is missing. Callers must
    handle that — falling back to the inline `contact.dnc_status` only.
    """
    global _registry, _registry_load_attempted
    if _registry_load_attempted:
        return _registry
    _registry_load_attempted = True

    db_path = os.getenv("FTC_DNC_DB_PATH", "").strip()
    if not db_path:
        log.info("FTC_DNC_DB_PATH not set — FTC scrubbing disabled")
        return None
    try:
        _registry = FtcDncRegistry.from_sqlite(db_path)
        log.info(
            "FTC DNC registry loaded from %s (area codes: %s)",
            db_path,
            ",".join(sorted(_registry.area_codes)) or "<empty>",
        )
    except Exception as exc:
        log.warning("Failed to load FTC DNC registry from %s: %s", db_path, exc)
        _registry = None
    return _registry


def reset_registry_for_tests() -> None:
    """Reset the cached registry. Test-only helper."""
    global _registry, _registry_load_attempted
    if _registry is not None:
        _registry.close()
    _registry = None
    _registry_load_attempted = False


def normalize_status(status: str | None) -> str:
    value = (status or "unknown").strip().lower()
    if value in {"clear", "blocked"}:
        return value
    return "unknown"


def scrub_phone(raw_phone: str | None) -> FtcDncCheckResult | None:
    """Run a phone number through the FTC DNC registry.

    Returns None if the registry is not configured. Otherwise returns the
    raw check result.
    """
    registry = _load_registry()
    if registry is None:
        return None
    return registry.check_phone(raw_phone)


def can_call(contact: EnrichedContact) -> DncDecision:
    if not contact.phone:
        return DncDecision(False, "unknown", "No phone number")

    status = normalize_status(contact.dnc_status)
    # If the enrichment provider already gave us a definitive answer, trust it.
    if status == "clear":
        return DncDecision(True, status, "DNC clear")
    if status == "blocked":
        return DncDecision(False, status, "Phone is DNC blocked")

    # Unknown — try the FTC registry before giving up.
    ftc_result = scrub_phone(contact.phone)
    if ftc_result is None:
        return DncDecision(False, status, "DNC status unknown")
    if ftc_result.status == "clear":
        return DncDecision(True, "clear", f"FTC scrubber: {ftc_result.reason}")
    if ftc_result.status == "blocked":
        return DncDecision(False, "blocked", f"FTC scrubber: {ftc_result.reason}")
    return DncDecision(False, "unknown", f"FTC scrubber: {ftc_result.reason}")
