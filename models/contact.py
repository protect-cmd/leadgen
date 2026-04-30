from __future__ import annotations
from dataclasses import dataclass
from models.filing import Filing


@dataclass
class EnrichedContact:
    filing: Filing
    phone: str | None = None
    email: str | None = None
    secondary_address: str | None = None
    estimated_rent: float | None = None
    property_type: str | None = None  # "residential" | "commercial"


@dataclass
class RoutingOutcome:
    action: str        # "proceed" | "skip" | "flag"
    tag: str           # GHL tag to apply
    pipeline: str = "" # "residential" | "commercial" | ""
