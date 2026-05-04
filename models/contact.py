from __future__ import annotations
from dataclasses import dataclass
from models.filing import Filing


@dataclass
class EnrichedContact:
    filing: Filing
    track: str = "ec"              # "ec" (landlord) | "ng" (tenant)
    phone: str | None = None
    email: str | None = None
    secondary_address: str | None = None
    estimated_rent: float | None = None
    property_type: str | None = None  # "residential" | "commercial"

    @property
    def contact_name(self) -> str:
        return self.filing.landlord_name if self.track == "ec" else self.filing.tenant_name

    @property
    def contact_first_name(self) -> str:
        return self.contact_name.strip().split()[0].title()


@dataclass
class RoutingOutcome:
    action: str        # "proceed" | "skip" | "flag"
    tag: str           # GHL tag to apply
    pipeline: str = "" # "residential" | "commercial" | ""
